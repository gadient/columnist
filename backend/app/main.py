from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent_api import router as agent_router
from .analytics_api import router as analytics_router, teams_router
from .api import router as boards_router, workspace_router
from .auth_api import router as auth_router
from .instances_api import instances_router
from .note_imports.api import router as note_imports_router
from .cognito import enforce_csrf, require_user
from .config import settings
from .db import initialize_database_file
from .feedback_api import feedback_router
from .migrations import run_migrations
from .tenancy import enforce_board_access, enforce_workspace_access

# uvicorn configures its own loggers and leaves the root logger at WARNING, so without this every
# `logger.info(...)` in this application is silently discarded — including the stdout fallback
# records of the chat and audit logs (their WARNING would appear; the INFO record it rescues
# would not). `force=True` because uvicorn may have already installed a root handler; without it basicConfig
# is a no-op and the level stays where it was.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    force=True,
)

app = FastAPI(
    title=settings.app_name,
    description="Backend API for boards, columns, cards, and members.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix=settings.api_prefix)


@api_router.get("/health", tags=["system"], summary="Health check")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Enforce authentication + tenant isolation exactly when Cognito is configured. Locally (Cognito
# disabled) these lists are empty, so every route stays open and dev is unchanged; with Cognito
# set, app routers require a valid token, and board/workspace routers additionally require that the
# caller can access the board/workspace in the path (tenancy.py).
# CSRF rides on every app router (cookie-auth needs it on unsafe methods); it's a no-op on safe
# methods, when Cognito is off, and for pure Bearer callers. Auth = require_user + CSRF.
auth_deps = ([Depends(require_user), Depends(enforce_csrf)] if settings.cognito_enabled else [])
board_deps = auth_deps + ([Depends(enforce_board_access)] if settings.cognito_enabled else [])
workspace_deps = auth_deps + ([Depends(enforce_workspace_access)] if settings.cognito_enabled else [])

app.include_router(api_router)                                     # /health — always open
app.include_router(auth_router, prefix=settings.api_prefix)        # /auth/config + /auth/me open; login/logout self-contained
app.include_router(workspace_router, prefix=settings.api_prefix, dependencies=workspace_deps)
app.include_router(boards_router, prefix=settings.api_prefix, dependencies=board_deps)
# Note imports are board-scoped by construction (a session is bound to one board), so they take
# board_deps — the same tenant check as any other /boards/{id}/* route, not merely auth_deps.
app.include_router(note_imports_router, prefix=settings.api_prefix, dependencies=board_deps)
app.include_router(agent_router, prefix=settings.api_prefix, dependencies=auth_deps)
app.include_router(analytics_router, prefix=settings.api_prefix, dependencies=auth_deps)
app.include_router(teams_router, prefix=settings.api_prefix, dependencies=auth_deps)
app.include_router(instances_router, prefix=settings.api_prefix, dependencies=auth_deps)  # invites self-gate (401 when Cognito off)
app.include_router(feedback_router, prefix=settings.api_prefix, dependencies=auth_deps)   # POST open to any signed-in user; GET is PIU-only


@app.on_event("startup")
def on_startup() -> None:
    # Fail closed by default. Auth is enforced whenever Cognito is configured; running WITHOUT auth
    # requires an explicit opt-in. We do NOT infer safety from the bind host (that is bypassable).
    # A half-configured Cognito is a misconfiguration, not "auth off".
    if settings.cognito_partially_configured:
        raise RuntimeError(
            "Refusing to start: Cognito is partially configured. Set all of "
            "COGNITO_REGION / COGNITO_USER_POOL_ID / COGNITO_APP_CLIENT_ID, or clear them."
        )
    if not settings.cognito_enabled and not settings.allow_insecure_no_auth:
        raise RuntimeError(
            "Refusing to start with authentication disabled. Configure COGNITO_* to enable auth, "
            "or set ALLOW_INSECURE_NO_AUTH=true for local development."
        )
    initialize_database_file()
    run_migrations()
    # Users only ever see "contact your administrator"; this line is where the administrator
    # learns what to set. Logged once here, and again on each refused chat or import request.
    agent_problem = settings.agent_setup_problem()
    if agent_problem:
        logging.getLogger(__name__).warning("The AI agents (chat, notes-to-cards) are off: %s", agent_problem)
