#!/usr/bin/env python3
"""Throwaway UI for watching the Notes-to-Cards agent. Drag a doc in, see what it proposes.

    bash scripts/try-extract-ui.sh      →  http://127.0.0.1:8010

Sanity-check tool, not product. Deliberately disposable: one file, no build step, no frontend
dependency, nothing else imports it. Delete `scripts/try-extract-ui.*` and nothing notices.

It calls the OpenAI SDK directly rather than through the provider seam (`note_imports/provider.py`),
and approximates owner resolution instead of running `note_imports/resolve.py`. The reader
(`extract.py`), the prompt (`prompt.py`) and the contract (`schema.py`) are the ones the API uses,
so the model's proposals are what the endpoint would get from the same model.

Needs OPENAI_API_KEY in backend/.env. A fraction of a cent per run.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import FastAPI, File, Form, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from app.config import settings  # noqa: E402
from app.note_imports.errors import ImportError as ImportFailure  # noqa: E402
from app.note_imports.extract import extract_docx, extract_pasted, extract_txt  # noqa: E402
from app.note_imports.prompt import (  # noqa: E402
    PROMPT_VERSION,
    build_locator_hint,
    build_system_prompt,
    build_user_message,
)
from app.note_imports.schema import (  # noqa: E402
    ModelRecommendationSet,
    confidence_band,
    openai_strict_json_schema,
)

app = FastAPI(title="notes-to-cards sanity check")

# A stand-in board. Two Alexes on purpose — that's what makes ambiguous-owner resolution visible.
COLUMNS = [
    {"id": "col_todo", "title": "To Do"},
    {"id": "col_doing", "title": "In Progress"},
    {"id": "col_review", "title": "Review"},
    {"id": "col_done", "title": "Done"},
]
MEMBERS = [
    {"id": "mem_1", "name": "Alice"},
    {"id": "mem_2", "name": "Bob"},
    {"id": "mem_3", "name": "Alex Chen"},
    {"id": "mem_4", "name": "Alex Rivera"},
]

SUITE = ROOT / "agent_test_suite"


def resolve_owner(raw: str) -> dict:
    """Mirror of the deterministic owner-resolution rules. The model never returns an id.

    This is the whole point of the design made visible: the model reports the text it read, and
    *this* decides who that is — deterministically, with no fuzzy matching. Not the real
    implementation (that is `note_imports/resolve.py`); close enough to show the behaviour.
    """
    name = raw.strip().lstrip("@").casefold()
    exact = [m for m in MEMBERS if m["name"].casefold() == name]
    if len(exact) == 1:
        return {"resolution": "existing_member", "memberId": exact[0]["id"], "alternatives": []}
    partial = [m for m in MEMBERS if name and name in m["name"].casefold()]
    if len(partial) == 1:
        return {"resolution": "existing_member", "memberId": partial[0]["id"], "alternatives": [],
                "rationale": f"matched {partial[0]['name']} on a partial name"}
    if len(partial) > 1:
        return {"resolution": "ambiguous", "memberId": None,
                "alternatives": [f"{m['name']} ({m['id']})" for m in partial]}
    return {"resolution": "unmatched", "memberId": None, "alternatives": []}


def read_any(name: str, raw: bytes):
    ext = Path(name).suffix.lower()
    if ext == ".docx":
        return extract_docx(raw, declared_ext=".docx")
    if ext == ".txt":
        return extract_txt(raw)
    if ext in (".doc", ".docm", ".rtf", ".pdf", ".odt"):
        raise ImportFailure("UNSUPPORTED_FILE_TYPE",
                            detail=f"{ext} is not supported — only .txt and .docx.")
    # .md and friends: plain UTF-8 in practice. The real endpoint rejects these on extension
    # — allowed here so the suite's .md files can be exercised. That gap is
    # an open question, not a decision.
    return extract_txt(raw)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    samples = sorted(p.name for p in SUITE.glob("*")) if SUITE.is_dir() else []
    return PAGE.replace("__SAMPLES__", json.dumps(samples)).replace(
        "__MEETING_DATE__", date.today().isoformat()
    )


@app.post("/analyze")
async def analyze(
    meetingDate: str = Form(...),
    file: UploadFile | None = File(None),
    sample: str | None = Form(None),
    pastedText: str | None = Form(None),
):
    # 1. Read the source with the real reader.
    try:
        if sample:
            p = SUITE / sample
            if not p.is_file():
                return JSONResponse({"error": {"code": "EMPTY_INPUT", "message": "no such sample"}}, 400)
            extraction = read_any(p.name, p.read_bytes())
            label = f"agent_test_suite/{p.name}"
        elif file is not None and file.filename:
            extraction = read_any(file.filename, await file.read())
            label = file.filename
        elif pastedText and pastedText.strip():
            extraction = extract_pasted(pastedText)
            label = "pasted text"
        else:
            return JSONResponse({"error": {"code": "EMPTY_INPUT", "message": "nothing supplied"}}, 400)
    except ImportFailure as exc:
        # Reader rejections land here — the point is that they cost nothing and never reach a model.
        return JSONResponse({"error": exc.detail, "stage": "read"}, exc.status_code)

    if not settings.openai_api_key:
        return JSONResponse({"error": {"code": "NO_KEY", "message": "OPENAI_API_KEY is empty in backend/.env"}}, 400)

    # 2. Call the model.
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": build_system_prompt(
                    members=MEMBERS, meeting_date=meetingDate)},
                {"role": "user", "content": build_user_message(
                    extraction.text, locator_hint=build_locator_hint(list(extraction.segments)))},
            ],
            response_format={"type": "json_schema", "json_schema": {
                "name": "recommendation_set", "strict": True, "schema": openai_strict_json_schema()}},
        )
    except Exception as exc:
        return JSONResponse({"error": {"code": "PROVIDER_ERROR", "message": str(exc)[:400]}}, 502)
    elapsed = time.monotonic() - t0
    raw = resp.choices[0].message.content or ""

    # 3. Validate against the contract. A failure here is where the repair path fires.
    try:
        parsed = ModelRecommendationSet.model_validate_json(raw)
    except Exception as exc:
        return JSONResponse({"error": {
            "code": "MODEL_OUTPUT_INVALID",
            "message": "The model's response did not match the contract.",
            "action": "This is where the bounded repair retry fires.",
            "detail": str(exc)[:1200]}, "rawOutput": raw[:4000]}, 502)

    # 4. Everything the server decides — never the model.
    # The destination column is the user's choice on the import form, defaulted to
    # leftmost. It is not a model output, so there is nothing to validate and nothing to block on.
    destination = COLUMNS[0]
    cards = []
    for r in parsed.recommendations:
        blocked: list[str] = []
        owners = []
        for a in r.assignees:
            res = resolve_owner(a.rawName)
            owners.append({"rawName": a.rawName, "confidence": a.confidence, **res})
            if res["resolution"] == "ambiguous":
                blocked.append(f"'{a.rawName}' matches {len(res['alternatives'])} members — pick one")
            elif res["resolution"] == "unmatched":
                blocked.append(f"'{a.rawName}' is not on this board — approve adding them, or unassign")

        ev = [{"excerpt": e.excerpt, "locator": e.locator,
               "locatable": e.excerpt.strip() in extraction.text} for e in r.evidence]
        if not all(e["locatable"] for e in ev):
            blocked.append("Evidence could not be found in the note — server would reject")

        cards.append({
            "title": r.title, "description": r.description,
            "targetColumnId": destination["id"],
            "columnTitle": destination["title"],
            "assignees": owners,
            "dueDate": r.dueDate.model_dump() if r.dueDate else None,
            "priority": r.priority.model_dump(),
            "evidence": ev, "reason": r.reason,
            "confidence": r.confidence, "band": confidence_band(r.confidence),
            "ambiguities": r.ambiguities,
            "blockedReasons": blocked,
        })

    return JSONResponse({
        "source": label,
        "segments": [{"locator": s.locator, "text": s.text} for s in extraction.segments],
        "warnings": list(extraction.warnings),
        "cards": cards,
        "contract": json.loads(raw),
        "stats": {
            "seconds": round(elapsed, 1),
            "inTokens": resp.usage.prompt_tokens,
            "outTokens": resp.usage.completion_tokens,
            "model": settings.openai_model,
            "promptVersion": PROMPT_VERSION,
            "chars": len(extraction.text),
        },
    })


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Notes-to-Cards — sanity check</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#e6e9ef;--dim:#8b93a7;--acc:#5b9dff;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--cyan:#39c5cf}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 ui-sans-serif,-apple-system,"SF Pro Text",Segoe UI,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600}
.sub{color:var(--dim);font-size:12px}
main{display:grid;grid-template-columns:340px 1fr;gap:0;height:calc(100vh - 53px)}
#left{border-right:1px solid var(--line);padding:16px;overflow:auto}
#right{padding:20px;overflow:auto}
#drop{border:2px dashed var(--line);border-radius:10px;padding:28px 14px;text-align:center;
color:var(--dim);cursor:pointer;transition:.15s}
#drop.hot{border-color:var(--acc);background:#5b9dff11;color:var(--txt)}
label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:16px 0 6px}
input,select,textarea,button{background:var(--panel);color:var(--txt);border:1px solid var(--line);
border-radius:6px;padding:7px 9px;font:inherit;width:100%}
textarea{min-height:80px;resize:vertical;font-size:12px}
button{cursor:pointer;background:var(--acc);border-color:var(--acc);color:#04101f;font-weight:600;margin-top:12px}
button:disabled{opacity:.5;cursor:wait}
.samp{padding:5px 8px;border-radius:5px;cursor:pointer;font-size:12px;color:var(--dim);
display:flex;justify-content:space-between;gap:8px}
.samp:hover{background:var(--panel);color:var(--txt)}
.samp .x{font-size:10px;opacity:.6}
.card{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--ok);
border-radius:8px;padding:14px;margin-bottom:12px}
.card.blocked{border-left-color:var(--warn)}
.card h3{margin:0 0 4px;font-size:14px}
.row{display:flex;gap:8px;margin:5px 0;font-size:12.5px}
.k{color:var(--dim);min-width:74px;flex-shrink:0}
.pill{display:inline-block;padding:1px 7px;border-radius:20px;font-size:10.5px;border:1px solid var(--line)}
.high{color:var(--ok);border-color:#3fb95055}.medium{color:var(--warn);border-color:#d2992255}
.low{color:var(--bad);border-color:#f8514955}
.ev{background:#0b0d11;border-radius:5px;padding:6px 8px;font-size:12px;color:var(--dim);margin:3px 0}
.ev b{color:var(--cyan);font-weight:500}
.block{background:#d2992215;border:1px solid #d2992244;border-radius:6px;padding:7px 9px;margin-top:8px;font-size:12px}
.err{background:#f8514915;border:1px solid #f8514955;border-radius:8px;padding:16px}
.err h3{margin:0 0 6px;color:var(--bad)}
.stats{color:var(--dim);font-size:12px;margin-bottom:14px;display:flex;gap:14px;flex-wrap:wrap}
details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
summary{cursor:pointer;color:var(--dim);font-size:12px}
pre{background:#0b0d11;padding:12px;border-radius:6px;overflow:auto;font-size:11.5px;max-height:420px}
.empty{color:var(--dim);text-align:center;padding:50px;border:1px dashed var(--line);border-radius:8px}
.warn{background:#d2992215;border:1px solid #d2992244;border-radius:6px;padding:8px 10px;font-size:12px;margin-bottom:12px}
</style></head><body>
<header><h1>Notes-to-Cards</h1><span class="sub">sanity check · nothing here touches a board</span></header>
<main>
<div id="left">
  <div id="drop">Drop a .txt / .docx here<br><span style="font-size:11px">or click to browse</span></div>
  <input type="file" id="file" hidden accept=".txt,.docx,.md">
  <label>Meeting date <span style="text-transform:none">— relative dates resolve against this</span></label>
  <input type="date" id="md" value="__MEETING_DATE__">
  <label>Or paste a note</label>
  <textarea id="paste" placeholder="@Alice, ship the thing by Friday…"></textarea>
  <button id="go">Analyze</button>
  <label>Test suite</label>
  <div id="samples"></div>
</div>
<div id="right"><div class="empty">Drop a document to see what the agent proposes.</div></div>
</main>
<script>
const SAMPLES=__SAMPLES__, right=document.getElementById('right'), drop=document.getElementById('drop'),
      fileEl=document.getElementById('file'), go=document.getElementById('go');
let picked=null;
document.getElementById('samples').innerHTML = SAMPLES.map(s=>{
  const ext=s.split('.').pop(), bad=['rtf','doc','docm','pdf'].includes(ext);
  return `<div class="samp" data-s="${s}"><span>${s}</span><span class="x">${bad?'unsupported':ext}</span></div>`;
}).join('');
document.querySelectorAll('.samp').forEach(e=>e.onclick=()=>run({sample:e.dataset.s}));
drop.onclick=()=>fileEl.click();
fileEl.onchange=e=>{ if(e.target.files[0]) run({file:e.target.files[0]}); };
['dragenter','dragover'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',e=>{ const f=e.dataTransfer.files[0]; if(f) run({file:f}); });
go.onclick=()=>{ const t=document.getElementById('paste').value; if(t.trim()) run({pastedText:t}); };

async function run(src){
  go.disabled=true; right.innerHTML='<div class="empty">Reading, then asking the model…</div>';
  const fd=new FormData(); fd.append('meetingDate', document.getElementById('md').value);
  if(src.file) fd.append('file', src.file);
  if(src.sample) fd.append('sample', src.sample);
  if(src.pastedText) fd.append('pastedText', src.pastedText);
  let r,j;
  try{ r=await fetch('/analyze',{method:'POST',body:fd}); j=await r.json(); }
  catch(e){ right.innerHTML=`<div class="err"><h3>Request failed</h3>${e}</div>`; go.disabled=false; return; }
  go.disabled=false;
  if(j.error){ render_err(j); return; }
  render(j);
}
function esc(s){ return (s??'').toString().replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])); }
function render_err(j){
  const e=j.error;
  right.innerHTML=`<div class="err"><h3>${esc(e.code||'Error')}</h3>
    <div>${esc(e.message||'')}</div>
    ${e.action?`<div style="margin-top:8px;color:var(--dim)">→ ${esc(e.action)}</div>`:''}
    ${e.detail?`<pre style="margin-top:10px">${esc(e.detail)}</pre>`:''}
    ${j.stage==='read'?`<div style="margin-top:10px;color:var(--dim);font-size:12px">
      Rejected before any model call — this cost nothing.</div>`:''}
    ${j.rawOutput?`<details open><summary>what the model actually returned</summary><pre>${esc(j.rawOutput)}</pre></details>`:''}
  </div>`;
}
function render(j){
  const s=j.stats;
  let h=`<div class="stats"><span><b>${esc(j.source)}</b></span><span>${s.chars} chars → ${j.segments.length} segments</span>
    <span>${s.seconds}s</span><span>${s.inTokens} in / ${s.outTokens} out</span>
    <span>${esc(s.model)}</span><span>prompt ${esc(s.promptVersion)}</span></div>`;
  j.warnings.forEach(w=>h+=`<div class="warn">${esc(w)}</div>`);
  if(!j.cards.length){
    h+=`<div class="empty"><b>No action items found.</b><br>
      <span style="font-size:12px">That's a valid answer — a note with nothing actionable should produce nothing.</span></div>`;
  }
  j.cards.forEach((c,i)=>{
    h+=`<div class="card ${c.blockedReasons.length?'blocked':''}">
      <h3>${i+1}. ${esc(c.title)}</h3>
      ${c.description?`<div style="color:var(--dim);font-size:12.5px;margin-bottom:8px">${esc(c.description)}</div>`:''}
      <div class="row"><span class="k">confidence</span><span class="pill ${c.band}">${c.confidence.toFixed(2)} ${c.band}</span>
        <span style="color:var(--dim);font-size:11px">advisory — never approves anything</span></div>
      <div class="row"><span class="k">column</span><span>${esc(c.columnTitle)}
        <span style="color:var(--dim);font-size:11px">your choice at import — the model was never asked</span></span></div>`;
    if(!c.assignees.length) h+=`<div class="row"><span class="k">owner</span><span style="color:var(--dim)">unassigned — the note named nobody</span></div>`;
    c.assignees.forEach(a=>{
      const v = a.resolution==='existing_member' ? `<span style="color:var(--ok)">→ ${esc(a.memberId)}</span>`
        : a.resolution==='ambiguous' ? `<span style="color:var(--warn)">→ ambiguous: ${a.alternatives.map(esc).join(', ')}</span>`
        : `<span style="color:var(--cyan)">→ not on this board</span>`;
      h+=`<div class="row"><span class="k">owner</span><span>"${esc(a.rawName)}" ${v}</span></div>`;
    });
    const d=c.dueDate;
    h+=`<div class="row"><span class="k">due</span><span>${d&&d.value?`${esc(d.value)} <span style="color:var(--dim)">← "${esc(d.rawText)}" (${esc(d.provenance)})</span>`
      : d&&d.rawText?`<span style="color:var(--dim)">none — "${esc(d.rawText)}" was too vague to resolve</span>`
      :'<span style="color:var(--dim)">none</span>'}</span></div>
      <div class="row"><span class="k">priority</span><span>${esc(c.priority.value)}
        <span style="color:var(--dim)">(${esc(c.priority.provenance)}${c.priority.rawText?`, from "${esc(c.priority.rawText)}"`:''})</span></span></div>`;
    c.evidence.forEach(e=>h+=`<div class="ev">${e.locatable?'✓':'<span style="color:var(--bad)">✗ not found in note</span>'}
      <b>[${esc(e.locator)}]</b> ${esc(e.excerpt)}</div>`);
    c.ambiguities.forEach(a=>h+=`<div class="row"><span class="k">ambiguity</span><span style="color:var(--warn)">${esc(a)}</span></div>`);
    if(c.blockedReasons.length) h+=`<div class="block"><b>Approve &amp; create is blocked:</b><br>${c.blockedReasons.map(esc).join('<br>')}</div>`;
    h+=`</div>`;
  });
  h+=`<details><summary>the contract — exactly what the model returned</summary><pre>${esc(JSON.stringify(j.contract,null,2))}</pre></details>
      <details><summary>what the reader saw (${j.segments.length} segments)</summary><pre>${esc(j.segments.map(s=>`[${s.locator}] ${s.text}`).join('\n'))}</pre></details>`;
  right.innerHTML=h;
}
</script></body></html>"""


if __name__ == "__main__":
    import uvicorn

    print("\n  notes-to-cards sanity check →  http://127.0.0.1:8010\n")
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
