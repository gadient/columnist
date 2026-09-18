"""Where does note-import streaming actually stall?

`profile-extract.py` profiles OpenAI and cannot answer this: a deployed instance runs the extractor
on Bedrock, and the whole question is how *Bedrock* chunks its response. Quality and behaviour are
per-backend, so an OpenAI measurement would be confidently irrelevant.

The symptom is that proposed cards appear in one burst rather than one at a time. Three very
different causes produce that same symptom, and they need different fixes:

  1. COARSE CHUNKING   — the model sends few, large deltas. The scanner cannot emit a card before
                         its JSON object closes, so everything lands at once. Inherent-ish to the
                         forced-`tool` strategy; the fix is a UI promise change or a strategy swap.
  2. MODEL LATENCY     — many small deltas, but the model is simply slow. Nothing to fix in the
                         stream; the fix is expectation-setting (progress, not per-card reveal).
  3. SCANNER BUFFERING — deltas arrive steadily and early, but cards emit late anyway. That would
                         be our bug, in stream_parse.

This times all three: when each delta arrives, how big it is, and when each card is emitted. It
reuses the real BedrockExtractionProvider request shape, so it measures the shipped path rather
than an approximation of it.

    AWS_REGION=us-east-1 BEDROCK_MODEL_ID=<model> \
      backend/.venv/bin/python scripts/profile-bedrock-stream.py agent_test_suite/testcase2.txt

Costs one Bedrock call (a few cents). Read-only: it creates nothing and touches no board.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# Cognito off: this is a local profiler, not an app request.
for _v in ("COGNITO_REGION", "COGNITO_USER_POOL_ID", "COGNITO_APP_CLIENT_ID"):
    os.environ.setdefault(_v, "")

from app.config import settings                                    # noqa: E402
from app.note_imports.prompt import build_system_prompt, build_user_message  # noqa: E402
from app.note_imports.provider import BedrockExtractionProvider    # noqa: E402
from app.note_imports.stream_parse import RecommendationStreamScanner  # noqa: E402

MEMBERS = [
    {"id": "m1", "name": "Alice Chen"},
    {"id": "m2", "name": "Morgan Blake"},
    {"id": "m3", "name": "Bob Ng"},
]


def main() -> int:
    note_path = Path(sys.argv[1] if len(sys.argv) > 1 else "agent_test_suite/testcase2.txt")
    if not note_path.is_file():
        print(f"no such note: {note_path}", file=sys.stderr)
        return 2
    note = note_path.read_text()

    model = os.environ.get("BEDROCK_MODEL_ID") or settings.bedrock_model_id
    strategy = os.environ.get("BEDROCK_STRUCTURED_OUTPUT") or settings.bedrock_structured_output
    region = os.environ.get("AWS_REGION") or settings.bedrock_region or "us-east-1"

    print(f"note      : {note_path}  ({len(note)} chars)")
    print(f"model     : {model}")
    print(f"strategy  : {strategy}")
    print(f"region    : {region}\n")

    provider = BedrockExtractionProvider(
        region=region, model_id=model, strategy=strategy,
        temperature=settings.openai_temperature, max_tokens=settings.bedrock_max_tokens,
    )
    system_prompt = build_system_prompt(members=MEMBERS, meeting_date="2026-07-16")
    user_message = build_user_message(note)

    client = provider._client()
    kwargs = provider._request_kwargs(system_prompt, user_message)
    scanner = RecommendationStreamScanner()

    t0 = time.perf_counter()
    deltas: list[tuple[float, int]] = []   # (elapsed, chars)
    cards: list[tuple[float, int]] = []    # (elapsed, delta index it closed on)

    response = client.converse_stream(**kwargs)
    for event in response["stream"]:
        if "contentBlockDelta" not in event:
            continue
        d = event["contentBlockDelta"]["delta"]
        chunk = d.get("text")
        if chunk is None and "toolUse" in d:
            chunk = d["toolUse"].get("input")
        if not chunk:
            continue
        now = time.perf_counter() - t0
        deltas.append((now, len(chunk)))
        for _ in scanner.feed(chunk):
            cards.append((time.perf_counter() - t0, len(deltas)))
    total = time.perf_counter() - t0

    if not deltas:
        print("no deltas received — nothing to measure")
        return 1

    sizes = [n for _, n in deltas]
    first, last = deltas[0][0], deltas[-1][0]
    gaps = [b[0] - a[0] for a, b in zip(deltas, deltas[1:])] or [0.0]

    print(f"total wall clock        : {total:6.2f}s")
    print(f"time to FIRST delta     : {first:6.2f}s   <- model thinking before writing")
    print(f"streaming window        : {last - first:6.2f}s   ({len(deltas)} deltas)")
    print(f"delta size  min/med/max : {min(sizes)} / {sorted(sizes)[len(sizes)//2]} / {max(sizes)} chars")
    print(f"delta gap   max         : {max(gaps):6.2f}s")
    print(f"cards emitted           : {len(cards)}")

    if cards:
        print("\ncard #   emitted at   after delta #")
        for i, (t, di) in enumerate(cards, 1):
            print(f"  {i:<5}  {t:7.2f}s     {di}/{len(deltas)}")
        spread = cards[-1][0] - cards[0][0]
        print(f"\nfirst card at {cards[0][0]:.2f}s, last at {cards[-1][0]:.2f}s (spread {spread:.2f}s)")

    print("\n--- reading ---")
    # Order matters. Test time-to-first-delta FIRST: when nearly all the wall clock precedes the
    # first byte, every downstream measure (card spread, delta gaps) is compressed as a
    # consequence, and a "cards bunched together" check fires misleadingly — blaming the scanner for
    # a stall that happened before the scanner ever ran.
    chars = sum(sizes)
    window = max(last - first, 1e-6)
    if first > 0.5 * total:
        print(f"UPSTREAM STALL: {first / total:.0%} of the wall clock elapsed before the first byte.")
        print(f"Then {chars} chars arrived in {window:.2f}s ({chars / window:,.0f} chars/s).")
        if chars / window > 2000:
            print("That rate is far above generation speed, so the tokens were produced during the")
            print("silence and FLUSHED at the end — buffering upstream of us, not slow generation.")
            print("Nothing in our code can unbuffer it. Compare strategies (tool vs json_schema)")
            print("to see whether the forced-tool path is what withholds the stream.")
        else:
            print("The rate is consistent with real generation, so the model simply thinks first.")
            print("The transport is fine; change what the UI promises, not the stream.")
    elif len(deltas) <= 3:
        print("COARSE CHUNKING: only a handful of deltas — the model sends the answer in a few")
        print("large pieces, so no card can surface early. Not fixable in our code.")
    elif cards and (cards[-1][0] - cards[0][0]) < 0.15 * window:
        print("BURST AT THE END: deltas arrive steadily across the window, yet every card lands")
        print("together. THIS one is ours — suspect stream_parse or the JSON shape.")
    else:
        print("STREAMING LOOKS HEALTHY here — cards spread across the window. If a deployed instance")
        print("still bursts, the difference is downstream (CloudFront/ALB), not the model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
