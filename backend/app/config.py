from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# backend/.env — resolved absolutely, from this file's location rather than the working directory.
# A bare ".env" only loads when the process happens to start in backend/; anything launched from
# the repo root (scripts/, tooling, a one-off python -c) silently got no .env at all and fell back
# to defaults — an empty API key or the wrong DB with no error to explain it.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "columnist-backend"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    sqlite_path: str = "../data/columnist.sqlite"
    # Deployment DB target. Empty ⇒ local SQLite (default). When set to a
    # postgres://…/postgresql://… URL, db.py switches to the psycopg backend (e.g. RDS when
    # deployed, a local Docker container for testing). Same code path, same migrations.
    database_url: str = ""
    # Agent model backend. One switch for BOTH agents (chat and notes-to-cards):
    #   "offline"   — no cloud model. Chat is UNAVAILABLE (there is no rules engine — it returns an
    #                 honest "not configured" message, not a keyword-matched answer);
    #                 notes-to-cards proposes nothing. Deterministic, free, the test-suite default.
    #   "openai"    — the OpenAI API with OPENAI_API_KEY; model from `openai_model`.
    #   "anthropic" — the Anthropic API with ANTHROPIC_API_KEY; model from `anthropic_model`.
    #   "bedrock"   — Amazon Bedrock's Converse API (boto3) on AWS credentials. The deployed backend
    #                 Note the `bedrock_model_id` DEFAULT below is Nova (a safe,
    #                 widely-available default); set BEDROCK_MODEL_ID to Sonnet 4.5 — see .env.example.
    #
    # ⚠️ Quality measured on one model does not transfer to another. The gates (90% precision /
    # 80% recall) were cleared on Claude Sonnet 4.5 via Bedrock; any other model needs its own run.
    agent_backend: str = "offline"

    # OpenAI (agent_backend="openai"). The key is a real secret: .env locally, never committed; the
    # pre-commit gitleaks hook blocks it.
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    # Anthropic API (agent_backend="anthropic"). Same secret handling as the OpenAI key. The default
    # is the model the published results were measured on (via Bedrock). Newer Claude models reject
    # the `temperature` parameter the providers send, so changing this can need a code change.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_max_tokens: int = 4096
    # Sampling temperature for extraction. Low on purpose: this is structured extraction with
    # confidence scoring, not creative writing — the same note should yield the same cards and the
    # same confidence, or the corpus numbers mean nothing and a reviewer can't trust a score
    # that swings each run. Left default (~1.0) the model over-produces and clusters confidence high.
    # Raise it only to deliberately survey variety. Every provider (chat and extraction, on all three
    # backends) sends this value, so the knob is provider-agnostic even though it is named for OpenAI.
    openai_temperature: float = 0.2

    # ── Amazon Bedrock (agent_backend="bedrock"). ──────────────────────────────────────────
    # boto3 resolves AWS credentials the standard way (env keys / AWS_PROFILE / SSO / `aws login` /
    # instance role) — no keys live here. Region: set bedrock_region (BEDROCK_REGION in .env).
    # Values in backend/.env are read by this Settings class only and are NOT exported to the
    # environment, so AWS_REGION / AWS_DEFAULT_REGION written in .env never reach boto3; and boto3
    # reads AWS_DEFAULT_REGION, not AWS_REGION, even from a real environment variable. Empty ⇒ the
    # region from the shell's AWS_DEFAULT_REGION or the active profile in ~/.aws/config.
    #
    # Model switchability is the whole point (see the probe: scripts/bedrock-probe.sh). Converse is
    # model-agnostic, so changing bedrock_model_id is how you switch models — no code change. The
    # extraction *temperature* reuses openai_temperature (0.2): the field is named for OpenAI but the
    # value is provider-agnostic and feeds Bedrock's inferenceConfig for the same reason (reproducible
    # extraction/scoring). Quality does NOT transfer across models — re-run corpus-eval per model.
    bedrock_region: str = ""                              # empty ⇒ boto3's resolved region
    bedrock_model_id: str = "us.amazon.nova-2-lite-v1:0"  # model ID or inference-profile ID
    # How we force schema-valid JSON, per what the model supports (confirmed via the probe):
    #   "tool"        — forced tool call (toolConfig + toolChoice). Nova (constrained decoding) + Claude.
    #   "json_schema" — native structured outputs (outputConfig.textFormat). Claude 4.5, Mistral,
    #                   DeepSeek, GPT-on-Bedrock, Qwen — NOT Nova/Llama. (GA Feb 2026.)
    bedrock_structured_output: str = "tool"
    bedrock_max_tokens: int = 4096                        # inferenceConfig.maxTokens ceiling
    # Escape hatch for model-specific Converse knobs passed as additionalModelRequestFields (raw
    # JSON object string). Leave empty for defaults. Use it to pin e.g. a reasoning-effort control on
    # a thinking model so corpus scoring stays deterministic — the field name is model-specific, so
    # we don't guess it here.
    bedrock_additional_request_fields: str = ""

    # Bedrock Guardrails for the chat agent. Empty = no guardrailConfig sent,
    # which is the default and what the test suite runs. This is an *addition* to the agent's
    # system prompt, not a replacement for it: a generic content filter has no opinion about the
    # rules that actually matter here (tool output is data not instructions; don't answer a nearby
    # question and present it as the answer). Creating the guardrail is an AWS-side task, so this
    # is wired but has never been exercised — the first live run is the real test.
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = ""      # empty ⇒ DRAFT

    # Chat agent loop bounds. All three fail closed: hitting one withholds the
    # answer rather than returning a partial. The token budget is per *request* and separate from
    # max_tokens_per_user_per_day — one question must not be able to spend someone's whole day.
    # Defaults are measured, not guessed: a live eval averaged 5.4k tokens and peaked at 11.7k
    # per question, so a budget of 8k would fail closed on correct answers.
    agent_max_turns: int = 5
    agent_token_budget: int = 30000
    agent_timeout_seconds: float = 45.0
    # Must not exceed `AgentChatInput.message`'s own `max_length` (schemas.py) — Pydantic rejects
    # first with a 422, so a larger value here is unreachable code that looks like a limit. Kept as
    # a second gate because `validate_agent_input` is also reachable from paths that do not go
    # through that schema; move both together.
    agent_max_message_chars: int = 1200

    # ── Feedback durability. ──────────────────────────────────────────────────────────────
    # Feedback lands in the database, which powers the in-app inbox — but a deployed database (e.g.
    # RDS) may be stopped to save money (`deploy/pause.sh` does this). That makes the feedback
    # readable only while the thing being complained about is running, which is backwards. Set a
    # bucket and every note is also written to S3 as its own object: readable from the console with
    # the app and database both down, and durable if the database is ever rebuilt.
    #
    # Must NOT be the frontend bucket — that one is served to the world through CloudFront.
    feedback_s3_bucket: str = ""             # empty ⇒ database only (local dev)
    feedback_s3_prefix: str = "feedback/"

    mcp_shared_secret: str = ""
    mcp_allowed_board_ids: str = ""
    mcp_max_result_limit: int = 50
    chat_log_path: str = "../data/logs/chat_completions.jsonl"
    audit_log_path: str = "../data/logs/audit.jsonl"
    mcp_pid_path: str = "../data/runtime/mcp_server.pid"

    # ── AWS Cognito (backend-for-frontend auth). ──────────────────────────────
    # Empty values ⇒ auth is disabled and every route stays open (current default).
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    cognito_app_client_secret: str = ""   # confidential-client secret — .env/SSM only, NEVER commit
    cognito_domain: str = ""            # (unused in the BFF flow; kept for optional Hosted UI later)

    # Session cookies (Backend-for-Frontend). The browser never sees a raw token — the backend
    # sets these httpOnly. `secure` must be True in production (HTTPS); False for local http dev.
    auth_cookie_name: str = "columnist_session"         # httpOnly — the Cognito id token
    auth_refresh_cookie_name: str = "columnist_refresh" # httpOnly — the refresh token
    auth_cookie_secure: bool = False                    # set AUTH_COOKIE_SECURE=true in prod
    auth_cookie_samesite: str = "lax"
    # MUST match the Cognito app-client id/access-token validity, else the cookie outlives the
    # token (or vice-versa) and calls 401 at the shorter expiry. Interim session-length fix:
    # 12h here + 12h token validity on the pool, until real token refresh
    # (deferred). Override with AUTH_COOKIE_TTL_SECONDS to keep them in lockstep.
    auth_cookie_ttl_seconds: int = 43200                 # 12h
    csrf_cookie_name: str = "columnist_csrf"            # readable by JS (double-submit CSRF)
    csrf_header_name: str = "X-CSRF-Token"

    # Cost guards. Enforced by the invite endpoints once auth is live.
    max_users: int = 5                    # UNUSED — read nowhere; superseded by the per-Instance caps below
    max_instances: int = 10               # private Instances the operator may create (excl. default)
    max_primary_users: int = 1            # PIUs per Instance — one user owns each Instance
    max_secondary_per_primary: int = 5    # SIUs each PIU may invite
    max_workspace_members: int = 5

    # Rate limiting / cost caps. A single daily token meter is shared across chat and
    # doc reading; a per-document cap bounds a single extraction.
    max_tokens_per_user_per_day: int = 100000
    max_tokens_per_document: int = 12000
    # Per-user exceptions to the daily cap, as `email=tokens` pairs:
    #   TOKEN_BUDGET_OVERRIDES="someone@example.com=200000,other@example.com=100000"
    # Config rather than a database column on purpose: an exception should be a deliberate,
    # reviewable act by whoever operates the deployment, not something grantable from inside the
    # running app. Emails are matched case-insensitively.
    #
    # ⚠️ These bypass the cost guard. 200k tokens/day is ~37 agent questions (~5.4k each) and, if
    # actually spent every day, can dominate a small deployment's model budget on its own.
    token_budget_overrides: str = ""

    # Explicit opt-in to run WITHOUT authentication (local dev only). Production must configure
    # Cognito instead. The startup guard fails closed unless this is true or Cognito is configured —
    # it does NOT infer safety from the bind host (that is bypassable). See `on_startup` in main.py.
    allow_insecure_no_auth: bool = False

    @property
    def token_budget_override_map(self) -> dict[str, int]:
        """`email -> daily token budget`, lower-cased. Malformed entries are skipped rather than
        raising: a typo in an ops variable must not stop the service booting, and the effect of a
        skipped entry is the default cap — the safe direction."""
        out: dict[str, int] = {}
        for entry in self.token_budget_overrides.split(","):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            email, _, raw = entry.partition("=")
            try:
                budget = int(raw.strip())
            except ValueError:
                continue
            if email.strip() and budget > 0:
                out[email.strip().lower()] = budget
        return out

    @property
    def cognito_enabled(self) -> bool:
        return bool(
            self.cognito_region.strip()
            and self.cognito_user_pool_id.strip()
            and self.cognito_app_client_id.strip()
        )

    @property
    def cognito_partially_configured(self) -> bool:
        """Some but not all Cognito fields set — a misconfiguration, treated as fail-closed."""
        parts = [
            self.cognito_region.strip(),
            self.cognito_user_pool_id.strip(),
            self.cognito_app_client_id.strip(),
        ]
        return any(parts) and not all(parts)

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.strip().lower().startswith(("postgres://", "postgresql://"))

    def agent_setup_problem(self) -> str | None:
        """Why the AI agents cannot run, written for the operator; None when a model is connected.

        Shared by chat, notes-to-cards and the startup log, so all three agree on what "connected"
        means. Users never see this text — they are told to contact their administrator, and the
        administrator finds this in the server log."""
        hint = "then restart the app; see the README section 'Turning on the AI agents'."
        if self.agent_backend not in ("openai", "anthropic", "bedrock"):
            return f"AGENT_BACKEND is {self.agent_backend!r}. Set it to openai, anthropic or bedrock in backend/.env, {hint}"
        if self.agent_backend == "openai" and not self.openai_api_key.strip():
            return f"AGENT_BACKEND is openai but OPENAI_API_KEY is empty. Add it to backend/.env, {hint}"
        if self.agent_backend == "anthropic" and not self.anthropic_api_key.strip():
            return f"AGENT_BACKEND is anthropic but ANTHROPIC_API_KEY is empty. Add it to backend/.env, {hint}"
        return None

    @property
    def sqlite_file(self) -> Path:
        backend_dir = Path(__file__).resolve().parent.parent
        return (backend_dir / self.sqlite_path).resolve()

    @property
    def allowed_mcp_board_ids(self) -> set[str]:
        return {
            board_id.strip()
            for board_id in self.mcp_allowed_board_ids.split(",")
            if board_id.strip()
        }

    @property
    def chat_log_file(self) -> Path:
        backend_dir = Path(__file__).resolve().parent.parent
        return (backend_dir / self.chat_log_path).resolve()

    @property
    def audit_log_file(self) -> Path:
        backend_dir = Path(__file__).resolve().parent.parent
        return (backend_dir / self.audit_log_path).resolve()

    @property
    def mcp_pid_file(self) -> Path:
        backend_dir = Path(__file__).resolve().parent.parent
        return (backend_dir / self.mcp_pid_path).resolve()


settings = Settings()
