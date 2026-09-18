# syntax=docker/dockerfile:1
#
# FastAPI backend image for AWS ECS Express (Fargate) on Graviton/ARM64.
#
# Build for ARM64 explicitly so the image matches the ECS task's runtimePlatform
# (cpuArchitecture=ARM64). On Apple Silicon this is native; in CI pass the flag:
#     docker buildx build --platform linux/arm64 -t <ecr-repo>:<tag> --push backend
#
# The task's runtimePlatform MUST also be ARM64, or Fargate rejects the image
# with "exec format error". See docs/DEPLOY.md.

# ── Stage 1: build wheels ─────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
# Pre-build all deps into wheels so the final stage installs with no toolchain.
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user; app owns only what it needs.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Application code (build context is the backend/ directory).
COPY app ./app
COPY migrations ./migrations

USER app
EXPOSE 8000

# Container-side health check; ECS also gets its own target-group health check.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

# Bind 0.0.0.0 so the ALB can reach it. Migrations run on app startup (main.py).
# Exec form (JSON) so uvicorn is PID 1 and receives SIGTERM directly for graceful
# shutdown during ECS rolling deploys. Port is fixed at 8000 (see EXPOSE / PORT).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
