#!/usr/bin/env python3
"""Where the time goes, and where the model's writing goes.

    bash scripts/profile-extract.sh agent_test_suite/testcase2.txt
    bash scripts/profile-extract.sh my-notes.docx --runs 3     # median of 3, with the spread

Answers two questions that arguments can't:

1. **Is analysis slow because the model is thinking, or because we asked for a lot?**
   It streams the response, so it can separate the time before the first word (the model reading
   the note) from the time spent writing. In one measured run the answer was emphatically the
   latter: 0.87s reading, 94% of the wall clock writing, at ~89 words/sec. Nothing is thinking.

2. **If we wanted the answer shorter, what would we cut?**
   It breaks the output down by which part of a card it went into. The top line is usually
   "quotes copied from your note" — the model re-typing sentences we sent it — which is what makes
   the locator-instead-of-quote idea worth its own measurement rather than a guess.

The decision to stream results rests on this tool's numbers. Re-run it before re-deciding that, and
re-run it whenever the prompt, the model, or the contract changes — all three move these numbers.

A development tool, not part of the product. It calls the OpenAI SDK directly rather than through
the provider seam (`note_imports/provider.py`), because timing needs the raw token stream and usage
counts, which the seam does not expose.

Needs OPENAI_API_KEY in backend/.env. Costs a fraction of a cent per run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.note_imports.extract import extract_docx, extract_txt  # noqa: E402
from app.note_imports.prompt import (  # noqa: E402
    PROMPT_VERSION,
    build_locator_hint,
    build_system_prompt,
    build_user_message,
)
from app.note_imports.schema import openai_strict_json_schema  # noqa: E402

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, CYAN = "\033[32m", "\033[33m", "\033[36m"

# Stand-in board. Members exist so the prompt is the real one; this tool measures cost and speed,
# not extraction quality (that's try-extract.sh).
MEMBERS = [
    {"id": "mem_1", "name": "Alice Chen"},
    {"id": "mem_2", "name": "Bob Martinez"},
    {"id": "mem_3", "name": "Alex Kim"},
    {"id": "mem_4", "name": "Alex Rivera"},
]

# What each part of a card is, in words a person would use. Keys are the contract's field names.
FRIENDLY = {
    "evidence": "quotes copied from your note",
    "description": "descriptions",
    "reason": "explanations of its thinking",
    "title": "titles",
    "assignees": "owner names",
    "dueDate": "dates + the phrase they came from",
    "priority": "priority + the phrase it came from",
    "ambiguities": "notes on what was unclear",
    "confidence": "confidence scores",
    "action": "the literal words 'create_card'",
}


def err(msg: str) -> int:
    print(f"\033[31merror\033[0m  {msg}", file=sys.stderr)
    return 1


def bar(frac: float, width: int = 24) -> str:
    return "█" * round(frac * width) + "·" * (width - round(frac * width))


def profile_once(enc, note_text: str, segments, meeting_date: str) -> dict:
    """One streamed call. Streaming is the only way to see the reading/writing split."""
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    system = build_system_prompt(members=MEMBERS, meeting_date=meeting_date)
    user = build_user_message(note_text, locator_hint=build_locator_hint(list(segments)))

    t0 = time.monotonic()
    first_token_at: float | None = None
    chunks: list[str] = []
    usage = None

    stream = client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "recommendation_set",
                "strict": True,
                "schema": openai_strict_json_schema(),
            },
        },
        stream=True,
        stream_options={"include_usage": True},
    )
    for chunk in stream:
        if chunk.usage:
            usage = chunk.usage
        if not chunk.choices:
            continue
        if delta := chunk.choices[0].delta.content:
            if first_token_at is None:
                first_token_at = time.monotonic()
            chunks.append(delta)

    total = time.monotonic() - t0
    ttft = (first_token_at - t0) if first_token_at else total
    raw = "".join(chunks)
    return {
        "ttft": ttft,
        "writing": total - ttft,
        "total": total,
        "raw": raw,
        "in_tok": usage.prompt_tokens if usage else len(enc.encode(system + user)),
        "out_tok": usage.completion_tokens if usage else len(enc.encode(raw)),
        "system": system,
        "user": user,
    }


def breakdown(enc, raw: str) -> tuple[dict[str, int], int, int]:
    """Tokens spent on each part of a card, summed across cards.

    Only the *values* are counted per field. Everything left over — field names, braces, quotes,
    commas — is reported as structure. Counting the key names into each field instead (the obvious
    first cut) double-counts: tokens don't compose across concatenation, so the parts summed to
    more than the whole and "structure" came out at -4.3%. A negative percentage is how you find
    out your accounting is wrong.
    """
    recs = json.loads(raw).get("recommendations", [])
    per_field: dict[str, int] = {}
    for r in recs:
        for key, val in r.items():
            body = val if isinstance(val, str) else json.dumps(val, separators=(",", ":"))
            per_field[key] = per_field.get(key, 0) + len(enc.encode(body))
    return per_field, len(enc.encode(raw)), len(recs)


def main() -> int:
    ap = argparse.ArgumentParser(description="Profile one extraction: time and output cost.")
    ap.add_argument("note", help="path to a .txt or .docx note")
    ap.add_argument("--runs", type=int, default=1, help="repeat and report the median + spread")
    ap.add_argument("--meeting-date", default=date.today().isoformat())
    args = ap.parse_args()

    if not settings.openai_api_key:
        return err("OPENAI_API_KEY is empty in backend/.env")

    path = Path(args.note)
    if not path.is_file():
        return err(f"no such file: {path}")

    try:
        import tiktoken
    except ImportError:
        return err("tiktoken missing — run via scripts/profile-extract.sh, which installs it")
    enc = tiktoken.get_encoding("o200k_base")

    # The non-model parts, timed for completeness. Spoiler: they round to zero.
    t = time.monotonic()
    data = path.read_bytes()
    t_read = time.monotonic() - t

    t = time.monotonic()
    try:
        extraction = extract_docx(data) if path.suffix.lower() == ".docx" else extract_txt(data)
    except Exception as exc:  # the reader's own rejections are informative here
        return err(f"the reader rejected this file: {exc}")
    t_extract = time.monotonic() - t

    print(f"\n{BOLD}note{RESET}  {path.name} · {len(extraction.text)} chars · "
          f"{len(extraction.segments)} segments")
    print(f"{BOLD}model{RESET} {settings.openai_model} · prompt {PROMPT_VERSION}\n")

    try:
        runs = [profile_once(enc, extraction.text, extraction.segments, args.meeting_date)
                for _ in range(args.runs)]
    except Exception as exc:
        return err(f"the provider call failed: {str(exc)[:300]}")

    runs.sort(key=lambda r: r["total"])
    r = runs[len(runs) // 2]
    if args.runs > 1:
        print(f"{DIM}{args.runs} runs — showing the median{RESET}\n")

    # ── where the time went ──────────────────────────────────────────────────────────────
    total = t_read + t_extract + r["total"]
    print(f"{BOLD}WHERE THE TIME WENT{RESET}")
    for label, secs, note in (
        ("read the file off disk", t_read, ""),
        ("pull the text out of it", t_extract, ""),
        ("model reads your note", r["ttft"], "before it writes a single word"),
        ("model writes the answer", r["writing"], f"{r['out_tok']} pieces"),
    ):
        pct = secs / total
        colour = GREEN if pct < 0.05 else (YELLOW if pct < 0.5 else CYAN)
        print(f"  {label:<26} {colour}{secs:>6.2f}s{RESET}  {bar(pct)} {pct*100:>4.1f}%  {DIM}{note}{RESET}")
    print(f"  {'':<26} {BOLD}{total:>6.2f}s{RESET}  total")
    if args.runs > 1:
        print(f"  {DIM}spread across {args.runs} runs: "
              f"{runs[0]['total']:.1f}s – {runs[-1]['total']:.1f}s{RESET}")

    speed = r["out_tok"] / r["writing"] if r["writing"] else 0
    print(f"\n  writing speed: {BOLD}{speed:.0f} pieces/sec{RESET} {DIM}(≈{speed*0.75:.0f} words/sec){RESET}")

    # ── where the writing went ───────────────────────────────────────────────────────────
    per_field, raw_tok, n_cards = breakdown(enc, r["raw"])
    if not n_cards:
        print(f"\n{DIM}no cards proposed — nothing to break down{RESET}\n")
        return 0

    print(f"\n{BOLD}WHERE THE WRITING WENT{RESET}  {DIM}{raw_tok} pieces across {n_cards} cards"
          f" · {raw_tok/n_cards:.0f} per card{RESET}")
    for key, tok in sorted(per_field.items(), key=lambda kv: -kv[1]):
        pct = tok / raw_tok
        print(f"  {FRIENDLY.get(key, key):<34} {tok:>5}  {bar(pct)} {pct*100:>4.1f}%")
    structure = raw_tok - sum(per_field.values())
    print(f"  {'JSON field names + punctuation':<34} {structure:>5}  "
          f"{bar(structure/raw_tok)} {structure/raw_tok*100:>4.1f}%")

    # ── the projection that actually matters ─────────────────────────────────────────────
    per_card_secs = r["writing"] / n_cards
    print(f"\n{BOLD}WHAT THIS MEANS AT SCALE{RESET}  {DIM}time tracks card count, not note size{RESET}")
    for n in (10, 25, 45):
        proj = r["ttft"] + per_card_secs * n
        flag = f"  {CYAN}← past a 60s sync ceiling{RESET}" if proj > 60 else ""
        print(f"  {n:>3} cards  ≈ {proj:>5.1f}s{flag}")

    # ── what we sent ─────────────────────────────────────────────────────────────────────
    sys_tok = len(enc.encode(r["system"]))
    note_tok = len(enc.encode(extraction.text))
    hint_tok = len(enc.encode(r["user"])) - note_tok
    print(f"\n{BOLD}WHAT WE SENT{RESET}  {DIM}{r['in_tok']} pieces — costs money, costs almost no time{RESET}")
    print(f"  {'the rules (system prompt)':<34} {sys_tok:>5}")
    print(f"  {'your note':<34} {note_tok:>5}")
    print(f"  {'the note AGAIN, with line numbers':<34} {hint_tok:>5}  {DIM}← sent twice, by design{RESET}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
