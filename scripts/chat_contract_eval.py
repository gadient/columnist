#!/usr/bin/env python3
"""Score the chat assistant's ANSWERS against the response contract, on a frozen fixture.

Complements `agent-tool-eval.py` (which grades tool *selection*). This grades the answer text —
bottom-line-first, Board → Column → Card, no field dump, no "nothing at risk" overreach, grounded —
with the model-free scorer in `app/agents/contract_score.py`. That is the behaviour the response
contract specifies.

Runs the REAL model against the frozen fixture with the clock pinned, so overdue/due-soon are
deterministic. The backend is AGENT_BACKEND (`--backend` overrides): bedrock needs AWS credentials,
openai / anthropic need their API key in backend/.env. Results cache to disk keyed on (backend,
model, question, prompt version), so re-scoring after a scorer change is free; only a PROMPT or model
change re-invokes the model. This is why the loop is cheap: iterate the prompt, pay once per question
per version, re-score for nothing.

    python3 scripts/chat_contract_eval.py               # real run, cached
    python3 scripts/chat_contract_eval.py --backend openai
    python3 scripts/chat_contract_eval.py --offline     # scorer self-check, no spend
    python3 scripts/chat_contract_eval.py --no-cache    # force fresh model calls
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

FIXTURE = BACKEND / "tests" / "fixtures" / "chat_eval_frozen_fixture.json"
RAW = BACKEND / "tests" / "fixtures" / "demo_boards_raw.json"
CACHE = Path(tempfile.gettempdir()) / "chat_contract_eval_cache"
ANCHOR = date(2026, 7, 20)

# (question, board_scoped). Drawn from the acceptance scenarios and from questions asked
# while using the demo workspace.
# board_scoped=False → workspace scope (activeBoardId=None).
QUESTIONS = [
    ("What's at risk this week?", False),
    ("What high priority tasks are due this week?", False),
    ("What needs leadership attention this week, and why?", False),
    ("Give me the top three items needing attention.", False),
    ("What is overdue?", False),
    ("What is blocked?", False),
    ("Who has the most on their plate?", False),
    ("Summarize the boards.", False),
    ("What needs attention?", True),
    ("Brief me for Monday's management meeting.", False),
]


def build_facts():
    """Compute contract ground truth from the frozen fixture."""
    from app.agents.contract_score import ContractFacts

    data = json.loads(FIXTURE.read_text())
    card_titles, board_titles, risk = set(), set(), set()
    for b in data["boards"]:
        board_titles.add(b["board"])
        for c in b["cards"].values():
            card_titles.add(c["title"])
            if c["completed"]:
                continue
            due = c.get("dueDate")
            overdue = bool(due) and due < ANCHOR.isoformat()
            due_soon = bool(due) and ANCHOR.isoformat() <= due <= (ANCHOR.replace(day=27)).isoformat()
            blocked = bool(c.get("blockedBy") or c.get("dependsOn"))
            if overdue or (c["priority"] == "high" and due_soon) or blocked:
                risk.add(c["title"])
    return ContractFacts(card_titles=card_titles, board_titles=board_titles, risk_card_titles=risk)


def seed_db():
    tmp = Path(tempfile.mkdtemp()) / "contract_eval.sqlite"
    from app.config import settings
    settings.sqlite_path = str(tmp)
    settings.database_url = ""
    # Cognito off for the eval: the fixture is a single workspace, so the tenant fence would be
    # every board anyway, and `run_agent` refuses to run unscoped WHILE Cognito is enabled. This is
    # an answer-quality eval, not an auth test — the fence has its own tests.
    settings.cognito_region = settings.cognito_user_pool_id = settings.cognito_app_client_id = ""
    from app import migrations, mcp_queries
    migrations.run_migrations()
    mcp_queries._utc_today = lambda: ANCHOR

    import importlib.util
    spec = importlib.util.spec_from_file_location("vpf", REPO / "scripts" / "verify_chat_eval_fixture.py")
    vpf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vpf)

    from app.db import get_connection
    conn = get_connection()
    try:
        vpf.build_db(conn, json.loads(RAW.read_text()), ANCHOR)
        board_id = conn.execute("SELECT id FROM boards WHERE title = 'Engineering'").fetchone()["id"]
    finally:
        conn.close()
    return board_id


def prompt_version(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="scorer self-check, no model")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--backend", choices=("bedrock", "openai", "anthropic"),
                    help="default: AGENT_BACKEND, else bedrock")
    ap.add_argument("--model", help="default: the backend's configured model")
    ap.add_argument("--region", default="us-east-1", help="bedrock only")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    board_id = seed_db()
    facts = build_facts()

    from app.agents import contract_score
    from app.agents.loop import DEFAULT_SYSTEM_PROMPT, run_agent

    ver = prompt_version(DEFAULT_SYSTEM_PROMPT)
    CACHE.mkdir(exist_ok=True)
    print(f"prompt version {ver}   fixture anchor {ANCHOR}   risk cards: {len(facts.risk_card_titles)}\n")

    from app.config import settings

    backend = args.backend or (
        settings.agent_backend if settings.agent_backend in ("bedrock", "openai", "anthropic") else "bedrock"
    )
    model = args.model or {
        "bedrock": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "openai": settings.openai_model,
        "anthropic": settings.anthropic_model,
    }[backend]
    if not args.offline:
        print(f"backend {backend}   model {model}\n")

    if args.offline:
        provider = None
    elif backend == "openai":
        from app.agents.openai_provider import OpenAIChatProvider
        provider = OpenAIChatProvider(api_key=settings.openai_api_key, model=model, temperature=0.0)
    elif backend == "anthropic":
        from app.agents.anthropic_provider import AnthropicChatProvider
        provider = AnthropicChatProvider(api_key=settings.anthropic_api_key, model=model, temperature=0.0,
                                         max_tokens=settings.anthropic_max_tokens)
    else:
        from app.agents.bedrock_provider import BedrockChatProvider
        provider = BedrockChatProvider(region=args.region, model_id=model, temperature=0.0,
                                       guardrail_id=settings.bedrock_guardrail_id,
                                       guardrail_version=settings.bedrock_guardrail_version)
    # Answers are per-model, so the cache must never serve one model's answer for another's.
    model_tag = f"{backend}-{hashlib.sha256(model.encode()).hexdigest()[:8]}"

    dim_pass = {d: 0 for d in contract_score.DIMENSIONS}
    total_pass = 0
    for question, board_scoped in QUESTIONS:
        key = CACHE / f"{ver}_{model_tag}_{hashlib.sha256(question.encode()).hexdigest()[:10]}.json"
        if provider is None:
            answer = "[offline] Two high-priority items need attention. 1. **Engineering → In Review → Fix race condition in job queue** — High; Marcus Chen."
        elif key.exists() and not args.no_cache:
            answer = json.loads(key.read_text())["answer"]
        else:
            out = run_agent(question, provider=provider,
                            active_board_id=(board_id if board_scoped else None))
            answer = out.text if out.complete else f"[incomplete: {out.stopped_because}]"
            key.write_text(json.dumps({"question": question, "answer": answer}))

        checks = contract_score.score(question, answer, facts)
        ok = contract_score.passed(checks)
        total_pass += ok
        for c in checks:
            dim_pass[c.name] += c.ok
        mark = "PASS" if ok else "FAIL"
        fails = ", ".join(f"{c.name}({c.detail})" for c in checks if not c.ok)
        print(f"  [{mark}] {question}")
        if fails:
            print(f"         ✗ {fails}")
        if args.verbose:
            print(f"         {answer[:300]}\n")

    n = len(QUESTIONS)
    print(f"\n{'='*60}\nCONTRACT SCORE: {total_pass}/{n} answers fully compliant\n{'='*60}")
    for d in contract_score.DIMENSIONS:
        print(f"  {d:20} {dim_pass[d]}/{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
