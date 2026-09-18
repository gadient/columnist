#!/usr/bin/env python3
"""Probe Bedrock for model access + structured-output support — BEFORE pointing the adapter at a model.

This is a throwaway diagnostic, not part of the product. It exists to answer three questions
against *your* AWS account (which this tool never mutates — it only reads and runs inference):

  1. Which text models / inference profiles can I actually reach in this region?
  2. For a given model, does the Feb-2026 structured-outputs mechanism
     (`outputConfig.textFormat` = a JSON schema) return schema-valid JSON — non-streaming AND
     streaming? (The adapter needs both.)
  3. If not, does the forced-tool mechanism (`toolConfig` + strict tool + `toolChoice: tool`) work
     instead? (Claude + Nova only, per the ToolChoice docs.)

Whichever mechanism passes here is the one to configure the adapter with for that model
(`BEDROCK_STRUCTURED_OUTPUT`); the adapter makes the same boto3 calls — so a green probe means the
adapter will work.

  # list what you can see (needs bedrock:ListFoundationModels / ListInferenceProfiles)
  python scripts/bedrock-probe.py --region us-east-1 list

  # probe one model — pass the ID/inference-profile ID exactly as `list` prints it
  python scripts/bedrock-probe.py --region us-east-1 probe --model us.anthropic.claude-sonnet-4-5-20250929-v1:0
  python scripts/bedrock-probe.py --region us-east-1 probe --model <id> --strategy both
  python scripts/bedrock-probe.py --region us-east-1 probe --model <id> --no-stream --raw

Credentials/region come from your environment the normal boto3 way (AWS_PROFILE / AWS_REGION /
`aws sso login` / etc.) — nothing is hardcoded and no credentials are read by anyone but boto3.

Run it via scripts/bedrock-probe.sh, which puts a *recent* boto3 in an isolated venv, independent of
the app's pinned boto3 (a boto3 older than this API rejects `outputConfig`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# A small schema that is *shaped like* the real recommendation contract (nested objects, an array,
# an enum, a number) — the parts that separate real structured-output support from a model that
# merely emits JSON-ish text. Deliberately self-contained so the probe stays fast and legible.
# `additionalProperties: false` on every object is mandatory for Bedrock structured outputs.
TEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "priority", "confidence", "evidence"],
            },
        }
    },
    "required": ["recommendations"],
}

TEST_SYSTEM = (
    "You extract concrete work items from a meeting note into the given schema. "
    "One object per task. `evidence` quotes the exact phrase from the note that supports the task. "
    "Return only what the note supports; if it names no tasks, return an empty list."
)

# Two obvious tasks, one clearly urgent — a right answer is ~2 recommendations, one high priority.
TEST_NOTE = (
    "Standup 2026-07-17. Alice will update the login page copy before Friday. "
    "Bob needs to fix the urgent billing bug that's blocking checkout — top priority."
)

TOOL_NAME = "record_recommendations"


# --------------------------------------------------------------------------------------------------
# pretty output
# --------------------------------------------------------------------------------------------------

def ok(msg: str) -> None:
    print(f"  \033[32m[ok]\033[0m   {msg}")


def fail(msg: str) -> None:
    print(f"  \033[31m[FAIL]\033[0m {msg}")


def info(msg: str) -> None:
    print(f"  \033[2m[info]\033[0m {msg}")


def hint(msg: str) -> None:
    print(f"         \033[2m↳ {msg}\033[0m")


# --------------------------------------------------------------------------------------------------
# error explanation — turn a boto3 exception into an actionable line
# --------------------------------------------------------------------------------------------------

def explain(exc: Exception) -> None:
    """Print a FAIL line plus a concrete next step for the common Bedrock failure modes."""
    from botocore.exceptions import (
        ClientError,
        EndpointConnectionError,
        NoCredentialsError,
        ParamValidationError,
    )

    if isinstance(exc, ParamValidationError):
        fail("botocore rejected the request shape")
        hint("Your boto3/botocore is too old for structured outputs. Run via scripts/bedrock-probe.sh "
             "(it installs a recent boto3 in an isolated venv).")
        return
    if isinstance(exc, NoCredentialsError):
        fail("no AWS credentials found")
        hint("Set AWS_PROFILE / AWS_REGION or run `aws sso login` in this shell first.")
        return
    if isinstance(exc, EndpointConnectionError):
        fail(f"could not reach Bedrock: {exc}")
        hint("Check the region value and your network.")
        return
    # NoRegionError isn't always importable by name across botocore versions — match by class name.
    if type(exc).__name__ == "NoRegionError":
        fail("no AWS region configured")
        hint("Pass --region us-east-1, or set AWS_REGION, or set a region in your AWS profile.")
        return
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        msg = exc.response.get("Error", {}).get("Message", "")
        fail(f"{code}: {msg}")
        if code in ("AccessDeniedException", "AccessDenied"):
            hint("Either the model access isn't granted (Bedrock console → Model access → request "
                 "access) or your IAM principal lacks bedrock:InvokeModel on this model ARN.")
        elif code == "ValidationException":
            if "inference profile" in msg.lower() or "on-demand" in msg.lower():
                hint("This model needs an inference-profile ID, not a bare model ID. Run the `list` "
                     "command and use an inferenceProfileId (e.g. us.anthropic.<model>).")
            elif "toolchoice" in msg.lower() or "tool" in msg.lower():
                hint("This model likely doesn't support forced tool choice. Try --strategy json_schema.")
            else:
                hint("The model may not support this mechanism. Try the other --strategy.")
        elif code in ("ResourceNotFoundException",):
            hint("Model/inference-profile ID not found in this region. Check `list` output and --region.")
        elif code in ("ThrottlingException", "TooManyRequestsException"):
            hint("Throttled — wait and retry, or check per-model quotas.")
        return
    fail(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------------------------------
# validation of a returned payload
# --------------------------------------------------------------------------------------------------

def validate_payload(text: str) -> tuple[bool, str]:
    """Return (ok, summary) for a JSON string that should match TEST_SCHEMA at a basic level."""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        return False, f"not valid JSON ({exc})"
    if not isinstance(obj, dict) or "recommendations" not in obj:
        return False, "JSON parsed but has no `recommendations` key"
    recs = obj["recommendations"]
    if not isinstance(recs, list):
        return False, "`recommendations` is not a list"
    for i, r in enumerate(recs):
        if not isinstance(r, dict):
            return False, f"recommendation[{i}] is not an object"
        missing = {"title", "priority", "confidence", "evidence"} - set(r)
        if missing:
            return False, f"recommendation[{i}] missing {sorted(missing)}"
        if r["priority"] not in ("low", "medium", "high"):
            return False, f"recommendation[{i}].priority '{r['priority']}' not in enum"
    titles = ", ".join(repr(r["title"]) for r in recs) or "(none)"
    return True, f"{len(recs)} recommendation(s): {titles}"


# --------------------------------------------------------------------------------------------------
# request builders — these mirror exactly what the adapter will send
# --------------------------------------------------------------------------------------------------

def _messages() -> list:
    return [{"role": "user", "content": [{"text": TEST_NOTE}]}]


def _system() -> list:
    return [{"text": TEST_SYSTEM}]


def json_schema_kwargs(model: str) -> dict:
    return {
        "modelId": model,
        "system": _system(),
        "messages": _messages(),
        "outputConfig": {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "name": "recommendation_set",
                        "description": "The set of work items extracted from the note.",
                        "schema": json.dumps(TEST_SCHEMA),
                    }
                },
            }
        },
    }


def tool_kwargs(model: str) -> dict:
    return {
        "modelId": model,
        "system": _system(),
        "messages": _messages(),
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": TOOL_NAME,
                        "description": "Record the extracted work items.",
                        "inputSchema": {"json": TEST_SCHEMA},
                    }
                }
            ],
            # Force this exact tool. Supported only by Anthropic Claude + Amazon Nova.
            "toolChoice": {"tool": {"name": TOOL_NAME}},
        },
    }


# --------------------------------------------------------------------------------------------------
# runners
# --------------------------------------------------------------------------------------------------

def _extract_text_nonstream(strategy: str, response: dict) -> tuple[str, str, dict]:
    """Pull the JSON string out of a non-streaming converse response for either strategy."""
    stop = response.get("stopReason", "?")
    usage = response.get("usage", {})
    blocks = response["output"]["message"]["content"]
    if strategy == "tool":
        for b in blocks:
            if "toolUse" in b:
                return json.dumps(b["toolUse"]["input"]), stop, usage
        return "", stop, usage
    # json_schema: the structured text is a normal text block
    for b in blocks:
        if "text" in b:
            return b["text"], stop, usage
    return "", stop, usage


def run_nonstream(rt, strategy: str, model: str, raw: bool) -> bool:
    label = "json_schema" if strategy == "json_schema" else "forced-tool"
    print(f"\n• non-streaming / {label}")
    kwargs = json_schema_kwargs(model) if strategy == "json_schema" else tool_kwargs(model)
    t0 = time.monotonic()
    try:
        resp = rt.converse(**kwargs)
    except Exception as exc:  # noqa: BLE001 — diagnostic: report everything, don't crash
        explain(exc)
        return False
    dt = time.monotonic() - t0
    text, stop, usage = _extract_text_nonstream(strategy, resp)
    if raw:
        info("raw payload: " + (text[:800] + ("…" if len(text) > 800 else "")))
    good, summary = validate_payload(text)
    (ok if good else fail)(summary)
    info(f"stopReason={stop}  latency={dt:.2f}s  "
         f"tokens in/out={usage.get('inputTokens','?')}/{usage.get('outputTokens','?')}")
    if stop not in ("end_turn", "tool_use", "stop", "complete"):
        hint(f"unexpected stopReason '{stop}' — for real notes this can mean truncation or refusal")
    return good


def run_stream(rt, strategy: str, model: str, raw: bool) -> bool:
    label = "json_schema" if strategy == "json_schema" else "forced-tool"
    print(f"\n• streaming / {label}")
    kwargs = json_schema_kwargs(model) if strategy == "json_schema" else tool_kwargs(model)
    t0 = time.monotonic()
    parts: list[str] = []
    stop = "?"
    usage: dict = {}
    first_byte = None
    try:
        stream = rt.converse_stream(**kwargs)
        for event in stream["stream"]:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"]["delta"]
                # json_schema → delta.text ; forced-tool → delta.toolUse.input (partial JSON string)
                chunk = delta.get("text")
                if chunk is None and "toolUse" in delta:
                    chunk = delta["toolUse"].get("input")
                if chunk:
                    if first_byte is None:
                        first_byte = time.monotonic() - t0
                    parts.append(chunk)
            elif "messageStop" in event:
                stop = event["messageStop"].get("stopReason", "?")
            elif "metadata" in event:
                usage = event["metadata"].get("usage", {})
    except Exception as exc:  # noqa: BLE001
        explain(exc)
        return False
    dt = time.monotonic() - t0
    text = "".join(parts)
    if raw:
        info("raw stream payload: " + (text[:800] + ("…" if len(text) > 800 else "")))
    good, summary = validate_payload(text)
    (ok if good else fail)(summary)
    info(f"stopReason={stop}  ttfb={'%.2fs' % first_byte if first_byte else 'n/a'}  "
         f"total={dt:.2f}s  tokens in/out={usage.get('inputTokens','?')}/{usage.get('outputTokens','?')}")
    return good


# --------------------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------------------

def cmd_list(args) -> int:
    import boto3

    bedrock = boto3.client("bedrock", region_name=args.region)
    print(f"\nText foundation models reachable in {args.region or '(region from profile/env)'}:")
    try:
        models = bedrock.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]
    except Exception as exc:  # noqa: BLE001
        explain(exc)
        return 1
    rows = []
    for m in models:
        life = m.get("modelLifecycle", {}).get("status", "")
        if life and life != "ACTIVE":
            continue
        rows.append((
            m["modelId"],
            m.get("providerName", ""),
            "stream" if m.get("responseStreamingSupported") else "no-stream",
            ",".join(m.get("inferenceTypesSupported", [])) or "-",
        ))
    width = max((len(r[0]) for r in rows), default=10)
    for model_id, provider, stream, inf in sorted(rows):
        print(f"  {model_id:<{width}}  {provider:<12} {stream:<9} {inf}")
    print(f"\n  {len(rows)} active text model(s). "
          "Models listing INFERENCE_PROFILE (not ON_DEMAND) must be called by inference-profile ID ↓")

    print("\nInference profiles (use these IDs with `probe --model`):")
    try:
        profiles = bedrock.list_inference_profiles().get("inferenceProfileSummaries", [])
    except Exception as exc:  # noqa: BLE001
        explain(exc)
        profiles = []
    if not profiles:
        print("  (none — or missing bedrock:ListInferenceProfiles permission)")
    for p in profiles:
        print(f"  {p['inferenceProfileId']:<45} {p.get('inferenceProfileName','')}")
    print("\nNext: python scripts/bedrock-probe.py --region", args.region,
          "probe --model <id-from-above>")
    return 0


def cmd_probe(args) -> int:
    import boto3

    rt = boto3.client("bedrock-runtime", region_name=args.region)
    print(f"\nProbing model: {args.model}   region: {args.region or '(from profile/env)'}")

    strategies = ["json_schema", "tool"] if args.strategy == "both" else [args.strategy]
    results: dict[str, bool] = {}
    for strat in strategies:
        results[f"{strat}/nonstream"] = run_nonstream(rt, strat, args.model, args.raw)
        if not args.no_stream:
            results[f"{strat}/stream"] = run_stream(rt, strat, args.model, args.raw)

    print("\n" + "=" * 60)
    any_pass = any(results.values())
    for name, passed in results.items():
        (ok if passed else fail)(name)
    if any_pass:
        winners = sorted({n.split("/")[0] for n, p in results.items() if p})
        print(f"\n\033[32m✓ Usable structured-output strategy for this model: "
              f"{', '.join(winners)}\033[0m")
        print("  → set BEDROCK_STRUCTURED_OUTPUT in backend/.env to a passing strategy (app default: tool).")
        return 0
    print("\n\033[31m✗ No strategy returned schema-valid output for this model.\033[0m")
    print("  → try a different model (see `list`) or the other --strategy.")
    return 1


def _add_region(ap: argparse.ArgumentParser) -> None:
    # SUPPRESS default so an omitted --region on the subparser doesn't clobber one given before the
    # subcommand. Accepted both before and after `list`/`probe`; resolved from the env otherwise.
    ap.add_argument("--region", default=argparse.SUPPRESS,
                    help="AWS region, e.g. us-east-1 (default: $AWS_REGION / $AWS_DEFAULT_REGION / profile)")


def main() -> int:
    p = argparse.ArgumentParser(description="Bedrock access + structured-output probe (read-only).")
    _add_region(p)
    sub = p.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="list reachable text models + inference profiles")
    _add_region(lp)

    pp = sub.add_parser("probe", help="test structured output against one model")
    _add_region(pp)
    pp.add_argument("--model", required=True, help="model ID or inference-profile ID (from `list`)")
    pp.add_argument("--strategy", choices=["json_schema", "tool", "both"], default="json_schema",
                    help="which structured-output mechanism to test (default: json_schema)")
    pp.add_argument("--no-stream", action="store_true", help="skip the streaming test")
    pp.add_argument("--raw", action="store_true", help="print the raw returned payload")

    args = p.parse_args()
    # Resolve region: explicit flag wins, else the standard boto3 env vars, else None (boto3 falls
    # back to the profile's region, and NoRegionError is handled with a clear hint).
    args.region = getattr(args, "region", None) or os.environ.get("AWS_REGION") \
        or os.environ.get("AWS_DEFAULT_REGION")
    try:
        if args.cmd == "list":
            return cmd_list(args)
        return cmd_probe(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
