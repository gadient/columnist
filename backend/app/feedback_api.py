"""In-app feedback.

Invited users may have no access to the source repository or an issue tracker. This gives them one
box to type in, and gives the Instance's primary user a way to read the results without database
access.

Two deliberate asymmetries:

* **Writing is permissive.** Anyone who can reach the app can leave feedback, attributed if we know
  who they are and anonymous if we do not (Cognito off, local dev). A note we cannot attribute is
  still worth strictly more than no note.
* **Reading is restricted.** Feedback is other people's words about your product, often including
  what they could not get working. Only the PIU of an Instance reads it, and only for their own
  Instance — the same boundary `instances_api` draws around the member roster.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from . import feedback_sink, store
from .config import settings
from .db import get_connection
from .schemas import FeedbackInput, FeedbackView
from .tenancy import current_user

feedback_router = APIRouter(prefix="/feedback", tags=["feedback"])


@feedback_router.post(
    "",
    response_model=FeedbackView,
    status_code=status.HTTP_201_CREATED,
    summary="Leave feedback",
)
def create_feedback_endpoint(
    payload: FeedbackInput,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> FeedbackView:
    conn = get_connection()
    try:
        row = store.create_feedback(
            conn,
            category=payload.category,
            message=payload.message,
            rating=payload.rating,
            page=payload.page,
            instance_id=(user or {}).get("instance_id"),
            user_id=(user or {}).get("id"),
            user_email=(user or {}).get("email"),
        )
    finally:
        conn.close()

    # Durable copy, outside the connection so a slow upload does not hold it open. Best-effort by
    # design: the database write above already succeeded, and losing the mirror is not worth
    # losing the note.
    feedback_sink.mirror(row)
    return FeedbackView(**row)


@feedback_router.get(
    "",
    response_model=list[FeedbackView],
    summary="Read feedback for your Instance (primary user only)",
)
def list_feedback_endpoint(
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> list[FeedbackView]:
    conn = get_connection()
    try:
        # Cognito off (local dev) is already an open, single-operator app — every other route
        # behaves this way, and pretending to enforce a role with no identities to check would be
        # theatre. With Cognito on, the role check is real.
        if not settings.cognito_enabled:
            return [FeedbackView(**row) for row in store.list_feedback(conn)]

        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        instance_id = user.get("instance_id")
        if not instance_id or store.member_role(conn, instance_id, user["id"]) != "piu":
            # 404 rather than 403: a secondary user has no business learning that a feedback
            # inbox exists for their Instance.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

        return [FeedbackView(**row) for row in store.list_feedback(conn, instance_id=instance_id)]
    finally:
        conn.close()
