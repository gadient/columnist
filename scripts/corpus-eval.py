#!/usr/bin/env python3
"""Score the notes-to-cards extractor against the labeled corpus.

    bash scripts/corpus-eval.sh                 # all fixtures, 5 runs each (cached)
    bash scripts/corpus-eval.sh --fixture text3
    bash scripts/corpus-eval.sh --runs 7 --verbose
    bash scripts/corpus-eval.sh --refresh       # ignore cache, re-call the model

A development/measurement tool, not part of the product and not part of `scripts/test.sh` (that
suite is free and model-free; this costs money and is nondeterministic). It runs the REAL provider
seam + resolver — exactly what the app produces — then hands the proposals to the model-free scorer
in `app.note_imports.corpus`, which is unit-tested separately.

Each of the N runs per fixture is a distinct model sample, cached by index so re-scoring is free.
The cache key folds in the model, temperature, and the exact prompts, so any prompt or note change
invalidates it automatically. Needs OPENAI_API_KEY in backend/.env (or AGENT_BACKEND set to a real
backend). Quality here does NOT transfer to Bedrock — the number is per-backend.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.note_imports.corpus import (  # noqa: E402
    GATE_PRECISION,
    GATE_RECALL,
    Aggregate,
    meets_gate,
    meets_safety_gate,
    score_run,
)
from app.note_imports.extract import extract_txt  # noqa: E402
from app.note_imports.prompt import (  # noqa: E402
    build_locator_hint,
    build_system_prompt,
    build_user_message,
)
from app.note_imports.provider import get_extraction_provider  # noqa: E402
from app.note_imports.resolve import resolve_recommendations  # noqa: E402
from app.note_imports.schema import ModelRecommendationSet  # noqa: E402

SUITE = ROOT / "agent_test_suite"
CACHE = SUITE / ".corpus-cache"

# ANSI (skipped when not a tty).
_TTY = sys.stdout.isatty()
def _c(code, s): return f"\x1b[{code}m{s}\x1b[0m" if _TTY else s
def green(s): return _c("32", s)
def red(s): return _c("31", s)
def yellow(s): return _c("33", s)
def dim(s): return _c("2", s)
def bold(s): return _c("1", s)


def _stringify_dates(obj):
    """YAML parses an unquoted `2026-07-16` into a date object; every comparison here is against ISO
    strings (`dueDate.value`, `meeting_date`). Coerce dates back to ISO so labels need no quoting."""
    if isinstance(obj, dict):
        return {k: _stringify_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_dates(v) for v in obj]
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # lazy: dev-only dependency
    except ModuleNotFoundError:
        sys.exit("[corpus-eval] PyYAML not installed. Run: pip install pyyaml")
    return _stringify_dates(yaml.safe_load(path.read_text()))


def _active_model() -> str:
    """The model the cache key must reflect. Per-backend, so scores don't collide across models —
    Nova 2 Lite and gpt-4.1-mini must never read each other's cache (quality is per-model)."""
    backend = (settings.agent_backend or "").strip().lower()
    if backend == "bedrock":
        return settings.bedrock_model_id
    if backend == "anthropic":
        return settings.anthropic_model
    return settings.openai_model


def _active_variant() -> str:
    """A second cache discriminator. On Bedrock the structured-output strategy changes the request
    (and only some strategies are even valid on a given boto3), so `tool` and `json_schema` results
    must not share a cache entry — that collision is what made a json_schema run silently read the
    tool cache and look identical."""
    if (settings.agent_backend or "").strip().lower() == "bedrock":
        return settings.bedrock_structured_output
    return ""


def _cached_extract(stem: str, idx: int, system_prompt: str, user_message: str, use_cache: bool) -> ModelRecommendationSet:
    key = hashlib.sha256(
        "|".join([stem, str(idx), _active_model(), _active_variant(),
                  str(settings.openai_temperature), system_prompt, user_message]).encode("utf-8")
    ).hexdigest()[:16]
    path = CACHE / f"{stem}.{idx}.{key}.json"
    if use_cache and path.exists():
        return ModelRecommendationSet.model_validate_json(path.read_text())
    provider = get_extraction_provider(settings)
    model_set = provider.extract(system_prompt=system_prompt, user_message=user_message)
    CACHE.mkdir(exist_ok=True)
    path.write_text(model_set.model_dump_json())
    return model_set


def _proposals_for_run(stem, spec, idx, use_cache) -> list[dict]:
    note_bytes = (SUITE / spec["note"]).read_bytes()
    extraction = extract_txt(note_bytes)
    system_prompt = build_system_prompt(members=[], meeting_date=spec["meeting_date"])
    user_message = build_user_message(
        extraction.text, locator_hint=build_locator_hint(list(extraction.segments))
    )
    model_set = _cached_extract(stem, idx, system_prompt, user_message, use_cache)
    # Resolve against an empty roster: owner matching needs a roster, but date/evidence scoring does
    # not, so this yields the exact RecommendationView the app would show. Resolution DOES now correct
    # the model's date arithmetic for a few unambiguous relative phrases (date_resolve), so `due_acc`
    # here reflects the shipped, post-resolution dates — not the model's raw guess.
    views = resolve_recommendations(
        model_set,
        members=[],
        target_column_id="col_corpus",
        normalized_note=extraction.text,
        meeting_date=spec["meeting_date"],
    )
    return [v.model_dump() for v in views]


def _pct(x: float) -> str:
    return f"{x * 100:4.0f}%"


def _gate_mark(ok: bool) -> str:
    return green("PASS") if ok else red("FAIL")


def _card_label(prop: dict) -> str:
    """A short identity for a proposal — title + the first evidence quote — so a spurious or
    duplicate card in --verbose is legible instead of just the word 'spurious'."""
    title = (prop.get("title") or "").strip()
    excerpt = ""
    for e in prop.get("evidence") or []:
        if e.get("excerpt"):
            excerpt = " ".join(e["excerpt"].split())
            break
    if len(excerpt) > 60:
        excerpt = excerpt[:60] + "…"
    return f'"{title}"' + (f"  ⟨{excerpt}⟩" if excerpt else "")


def _field_values(prop: dict) -> str:
    """The model's actual due/confidence — shown next to a `bad:` flag so we can see WHY it failed."""
    due = (prop.get("dueDate") or {}).get("value")
    return f"due={due!r} conf={prop.get('confidence')}"


def report(agg: Aggregate, verbose: bool, run_proposals: list[list[dict]] | None = None,
           *, gating: bool = True, known_gap: str | None = None) -> None:
    rmin, rmax = agg.recall_range
    pmin, pmax = agg.precision_range
    n_req = len(agg.runs[0].required_ids) if agg.runs else 0
    cards = sum(r.n_proposals for r in agg.runs) / len(agg.runs) if agg.runs else 0

    if gating:
        status = f"gate {int(GATE_PRECISION*100)}/{int(GATE_RECALL*100)}: {_gate_mark(meets_gate(agg))}"
    else:
        # Non-gating: quality is MEASURED but not pass/fail; only safety is enforced.
        gap = f", {known_gap}" if known_gap else ""
        status = f"{yellow('MEASURED')} (non-gating{gap}; safety {_gate_mark(meets_safety_gate(agg))})"

    print(f"\n{bold(agg.fixture)}   runs={len(agg.runs)}  cards≈{cards:.1f}   → {status}")
    print(f"  recall       mean {_pct(agg.recall_mean)}  {dim(f'(range {_pct(rmin).strip()}-{_pct(rmax).strip()}, {n_req} required)')}")
    print(f"  precision    mean {_pct(agg.precision_mean)}  {dim(f'(range {_pct(pmin).strip()}-{_pct(pmax).strip()})')}")
    supp = agg.worst_forbidden
    print(f"  suppression  worst {supp} forbidden hit(s)   {_gate_mark(supp == 0)}")
    print(f"  injection    {_gate_mark(agg.injection_ok)}")
    subs = []
    for name, acc in (("owner", agg.owner_acc), ("due", agg.due_acc), ("confidence", agg.confidence_acc)):
        subs.append(f"{name} {_pct(acc).strip() if acc is not None else '—'}")
    print(f"  {dim('fields:')}     {'   '.join(subs)}    {dim(f'duplicates {agg.duplicates_total} across runs')}")

    if verbose:
        for i, run in enumerate(agg.runs):
            props = run_proposals[i] if run_proposals else None
            print(dim(f"  run {i}:"))
            for v in run.verdicts:
                mark = {"matched": green("✓"), "duplicate": yellow("dup"),
                        "spurious": yellow("spurious"), "forbidden": red("FORBIDDEN")}[v.kind]
                tag = v.expected_id or v.forbidden_id or ""
                fails = [k for k, ok in v.field_checks.items() if not ok]
                fail_s = red(" bad:" + ",".join(fails)) if fails else ""
                detail = ""
                prop = props[v.index] if props and 0 <= v.index < len(props) else None
                if prop is not None:
                    if v.kind in ("spurious", "duplicate"):
                        detail = "  " + _card_label(prop)
                    elif fails:
                        detail = dim("  " + _field_values(prop))
                print(f"      {mark} {dim(tag)}{fail_s}{detail}")
            for f in run.invariant_failures:
                print(f"      {red('INVARIANT')} {f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Score the extractor against the labeled corpus.")
    ap.add_argument("--fixture", help="stem to run (e.g. text3); default all *.expected.yaml")
    ap.add_argument("--runs", type=int, default=5, help="samples per fixture (default 5); more "
                    "samples shrink the mean's standard error so the gate turns on true quality, "
                    "not a lucky draw on one borderline card")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache; re-call the model")
    ap.add_argument("--verbose", action="store_true", help="per-run, per-card verdicts")
    args = ap.parse_args()

    specs = sorted(SUITE.glob(f"{args.fixture}.expected.yaml" if args.fixture else "*.expected.yaml"))
    if not specs:
        return print("no *.expected.yaml fixtures found") or 1

    print(dim(f"backend={settings.agent_backend}  model={_active_model()}  temp={settings.openai_temperature}  cache={'off' if args.refresh else 'on'}"))
    if settings.agent_backend == "offline":
        print(yellow("[corpus-eval] AGENT_BACKEND=offline proposes nothing — everything will score 0. "
                     "Run with AGENT_BACKEND=openai (or a real backend)."))
    results = []  # (stem, ok, gating) — ok folds in the right gate for each fixture
    for spec_path in specs:
        stem = spec_path.name.replace(".expected.yaml", "")
        spec = _load_yaml(spec_path)
        gating = spec.get("gate", True)
        known_gap = spec.get("known_gap")
        runs, run_proposals = [], []
        for i in range(args.runs):
            props = _proposals_for_run(stem, spec, i, not args.refresh)
            run_proposals.append(props)
            runs.append(score_run(stem, props, spec))
        agg = Aggregate(stem, runs)
        report(agg, args.verbose, run_proposals, gating=gating, known_gap=known_gap)
        # A gating fixture must clear the full gate; a known-gap fixture only its safety half.
        ok = meets_gate(agg) if gating else meets_safety_gate(agg)
        results.append((stem, ok, gating))

    all_pass = all(ok for _, ok, _ in results)
    measured = [stem for stem, _, gating in results if not gating]
    print("\n" + bold("corpus: ") + (green("ALL FIXTURES PASS") if all_pass else red("SOME FIXTURES BELOW GATE")))
    if measured:
        print(dim(f"({len(measured)} non-gating fixture(s) measured, not gated: {', '.join(measured)} "
                  "— safety enforced, quality reported.)"))
    print(dim("(quality is per-backend; the gate applies to the shipped model, not this one.)\n"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
