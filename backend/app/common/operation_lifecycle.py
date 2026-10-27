# Creado por Aldo Garcia.
"""Terminal transitions and measured SQL lifecycle times, without prompt telemetry.

Execution is time in ``running`` until the observed terminal transition, including
authorization, inference admission and publication. It is not model latency or
TTFT. Recovery/expiry cannot identify when a lost worker actually stopped, so
their execution/total durations stay unknown. Historical rows are not backfilled.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.common.ids import utcnow_naive
from app.database.models import ChatOperation

PENDING = ("queued", "running")
TERMINAL = ("completed", "failed", "cancelled", "expired")


def elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    """A missing/reversed wall clock is unknown, never a fabricated zero."""
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds() * 1000)


def finalize_operations(
    db: Session, *criteria: Any, status: str, execution_end_known: bool = True,
    **fields: Any,
) -> int:
    """Finish only pending rows in the caller's transaction; repeated calls are no-ops.

    ``finished_at`` is when the terminal state was recorded, even for recovery.
    Payload publication remains atomic with conversation writes. Read only the
    columns needed for durations; never load prompts for metrics or logging.
    """
    if status not in TERMINAL:
        raise ValueError("Unsupported terminal operation state")
    allowed = {"response", "error_code"}
    if fields.keys() - allowed:
        raise ValueError("Unsupported terminal operation fields")
    rows = db.execute(
        select(ChatOperation.id, ChatOperation.status, ChatOperation.created_at, ChatOperation.started_at)
        .where(ChatOperation.status.in_(PENDING), *criteria).with_for_update()
    ).all()
    finished = utcnow_naive()
    count = 0
    for row in rows:
        # A queued job has a known waiting duration even when it expires without
        # ever executing. A recovered running job has no known execution end.
        never_started = row.status == "queued" and row.started_at is None
        queue_end = row.started_at if row.started_at is not None else finished if never_started else None
        queue_ms = elapsed_ms(row.created_at, queue_end)
        execution_ms = elapsed_ms(row.started_at, finished) if execution_end_known else None
        total_ms = elapsed_ms(row.created_at, finished) if execution_end_known or never_started else None
        changed = db.execute(
            update(ChatOperation)
            .where(ChatOperation.id == row.id, ChatOperation.status.in_(PENDING))
            .execution_options(synchronize_session=False)
            .values(
                status=status, finished_at=finished, queue_ms=queue_ms,
                execution_ms=execution_ms, total_ms=total_ms,
                message=None, session_id=None, **fields,
            )
        )
        count += changed.rowcount
        if changed.rowcount:
            # ORM bulk synchronization may leave never-loaded nullable attributes
            # as None in expire_on_commit=False sessions. Invalidate only this
            # row's changed attributes, without discarding other pending writes.
            instance = db.identity_map.get(db.identity_key(ChatOperation, row.id))
            if instance is not None:
                db.expire(instance, [
                    "status", "finished_at", "queue_ms", "execution_ms", "total_ms",
                    "message", "session_id", *fields,
                ])
    return count
