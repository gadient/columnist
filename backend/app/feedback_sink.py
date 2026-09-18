"""Mirror feedback to S3 so it outlives the app that collected it.

The database copy powers the in-app inbox and is the transactional record. A deployed database
(e.g. RDS) may be stopped to save money — `deploy/pause.sh` does this — so a database-only design
makes feedback readable only while the product being complained about is running, and loses it
entirely if the instance is ever rebuilt.

Each note becomes its own object. No appending, no read-modify-write, so two people submitting at
the same moment cannot overwrite each other. The key sorts chronologically and shows the date and
category in a console listing, which is the whole point: the operator opens the bucket and reads,
with nothing else running.

**Failing here must never fail the request.** Bookkeeping that runs after the real work has
succeeded must not turn that success into a 500. Feedback that reached the database is feedback we
have; a mirror that cannot write is a warning, not an error.
"""
from __future__ import annotations

import json
import logging

from .config import settings

logger = logging.getLogger(__name__)

# One warning per process rather than one per submission — a broken sink should be visible in the
# log, not drown it.
_SINK_BROKEN = False


def _object_key(row: dict) -> str:
    """`feedback/2026-07-20/2026-07-20T14-43-28.512Z__chat__fb_abc123.json`

    Date-partitioned and chronologically sortable, with the category in the name so a listing is
    skimmable without opening anything.
    """
    created = str(row.get("createdAt") or "")
    day = created[:10] or "undated"
    stamp = created.replace(":", "-") or "unstamped"
    category = row.get("category") or "general"
    return f"{settings.feedback_s3_prefix}{day}/{stamp}__{category}__{row.get('id')}.json"


def mirror(row: dict) -> None:
    """Best-effort copy of one feedback row to S3. Never raises."""
    global _SINK_BROKEN

    if not settings.feedback_s3_bucket.strip() or _SINK_BROKEN:
        return

    try:
        import boto3  # lazy: local dev has no bucket configured and should not pay the import

        boto3.client("s3").put_object(
            Bucket=settings.feedback_s3_bucket.strip(),
            Key=_object_key(row),
            Body=json.dumps(row, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        _SINK_BROKEN = True
        logger.warning(
            "could not mirror feedback to s3://%s — the database copy is unaffected; "
            "check the bucket name and that the task role has s3:PutObject on it",
            settings.feedback_s3_bucket,
            exc_info=True,
        )
