#!/usr/bin/env bash
# Run the chat agent's tool-selection eval against a real model (bedrock, openai or anthropic —
# AGENT_BACKEND picks it, `--backend` overrides).
#
# Answers one question the loop's own tests structurally cannot: which tool does a real model pick?
# The tool descriptions in agents/tools.py are the only thing the model reads when choosing, so this
# is the evidence about whether they work.
#
#   bash scripts/agent-tool-eval.sh                    # full run, roughly $0.40
#   bash scripts/agent-tool-eval.sh --offline          # free self-check, no AWS
#   bash scripts/agent-tool-eval.sh --verbose          # print the answers too
#   bash scripts/agent-tool-eval.sh --only overdue     # one question while iterating
#   bash scripts/agent-tool-eval.sh --backend openai   # OPENAI_API_KEY from backend/.env
#
# On openai/anthropic, the key comes from backend/.env and the AWS preflight below is skipped.
# On bedrock, needs AWS credentials resolvable by boto3 — env vars, a named profile, ~/.aws/credentials, an
# instance role, or SSO. However you normally authenticate is fine; the preflight below asks boto3
# what it found rather than assuming a particular mechanism.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/backend/.venv"
PY="$VENV_DIR/bin/python"

[ -x "$PY" ] || { echo "[agent-eval] No backend virtualenv. Run scripts/dev-up.sh once first." >&2; exit 1; }

# Expired credentials make the AWS SDKs hang on the EC2 metadata endpoint rather than erroring.
# Fail fast instead of looking wedged.
export AWS_EC2_METADATA_DISABLED=true

OFFLINE=0
BACKEND=""
prev=""
for arg in "$@"; do
  [ "$arg" = "--offline" ] && OFFLINE=1
  case "$arg" in --backend=*) BACKEND="${arg#--backend=}" ;; esac
  [ "$prev" = "--backend" ] && BACKEND="$arg"
  prev="$arg"
done
# Same resolution as the runner: --backend, else AGENT_BACKEND from the environment, else from
# backend/.env; anything that is not a chat backend means bedrock.
if [ -z "$BACKEND" ]; then
  BACKEND="${AGENT_BACKEND:-$(sed -n 's/^AGENT_BACKEND=\([a-z]*\).*/\1/p' "$ROOT_DIR/backend/.env" 2>/dev/null | tail -n 1)}"
fi
case "$BACKEND" in openai|anthropic) ;; *) BACKEND=bedrock ;; esac

if [ "$OFFLINE" -eq 0 ] && [ "$BACKEND" = "bedrock" ]; then
  # Ask boto3 what it resolves rather than sniffing for one mechanism's env vars. Checking for
  # AWS_ACCESS_KEY_ID/AWS_PROFILE only catches the SSO-export style and wrongly rejects a working
  # ~/.aws/credentials, a named profile, or an instance role. This is a local config read — it
  # makes no AWS API call and confirms nothing about permissions, only that credentials exist.
  set +e
  "$PY" - <<'PREFLIGHT'
import sys

try:
    import boto3
except ImportError:
    print("[agent-eval] boto3 missing from the backend venv — pip install -r backend/requirements.txt", file=sys.stderr)
    sys.exit(2)

session = boto3.Session()
creds = session.get_credentials()
if creds is None:
    print(
        "[agent-eval] boto3 found no AWS credentials.\n"
        "             Authenticate however you normally do, then re-run — boto3 accepts env vars,\n"
        "             a named profile (AWS_PROFILE=...), ~/.aws/credentials, an instance role, or SSO.\n"
        "             Or use --offline to self-check the harness for free.",
        file=sys.stderr,
    )
    sys.exit(2)

region = session.region_name or "unset — the runner defaults to us-east-1"
print(f"[agent-eval] credentials: {creds.method} | region: {region}")

# awscrt is needed only by some SSO credential paths; exit 3 asks the shell to install it rather
# than making every other auth mechanism pay for a dependency it will never load.
if creds.method.startswith("sso"):
    try:
        import awscrt  # noqa: F401
    except ImportError:
        sys.exit(3)
PREFLIGHT
  status=$?
  set -e
  case "$status" in
    0) ;;
    3) echo "[agent-eval] Installing awscrt into the backend venv (SSO credential dependency)…"
       "$VENV_DIR/bin/pip" install --quiet awscrt ;;
    *) exit 1 ;;
  esac
fi

OUT="$ROOT_DIR/data/agent-tool-eval-$(date +%Y%m%d-%H%M%S).json"
exec "$PY" "$ROOT_DIR/scripts/agent-tool-eval.py" --json "$OUT" "$@"
