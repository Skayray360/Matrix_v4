# Creado por Aldo Garcia.
"""Rechazo predictivo con duraciones medidas; SQL y HTTP sinteticos, sin Ollama."""
from contextlib import ExitStack

import pytest
from sqlalchemy import event, func, select

from app.agents import chat_queue
from app.common.ids import new_id, utcnow_naive
from app.database.models import ChatOperation, Conversation
from app.security.admission import admission
from tests.test_chat_queue import authenticated_client, controlled_queue, queue_db, submit

__all__ = ["controlled_queue", "queue_db"]
pytestmark = pytest.mark.unit


def history(factory, durations, *, status="completed"):
    with factory() as db:
        for duration in durations:
            db.add(ChatOperation(
                id=new_id(), user_id=new_id(), conversation_id=new_id(),
                request_hash="a" * 64, authorization_scope="b" * 64,
                status=status, finished_at=utcnow_naive(), execution_ms=duration,
            ))
        db.commit()


def test_rejects_before_acceptance_when_fifo_wait_reaches_deadline(queue_db, controlled_queue):
    history(queue_db, [90_000])
    controlled_queue.settings.chat_queue_max_size = 50
    controlled_queue.settings.chat_queue_timeout_seconds = 180
    with ExitStack() as stack:
        clients = [stack.enter_context(authenticated_client(queue_db)) for _ in range(3)]
        occupied = stack.enter_context(ExitStack())
        for _ in range(2):
            occupied.enter_context(admission("chat", 2))
        assert submit(clients[0], "wait-first-123456").status_code == 202
        assert submit(clients[1], "wait-second-123456").status_code == 202
        rejected = submit(clients[2], "wait-rejected-123456")
        assert rejected.status_code == 503, rejected.text
        assert rejected.json()["code"] == "chat_capacity_full"
        assert "espera estimada" in rejected.json()["message"]
        assert rejected.headers["Retry-After"]
        assert clients[2].operation("wait-rejected-123456") is None
        with queue_db() as db:
            assert db.scalar(select(func.count()).select_from(Conversation)) == 2
            assert db.scalar(select(func.count()).select_from(ChatOperation).where(ChatOperation.status == "queued")) == 2
        assert not controlled_queue.launches
        # El rechazo no consume la clave ni deja una conversacion vacia. Con
        # capacidad liberada, el mismo intento puede aceptarse normalmente.
        occupied.close()
        retried = submit(clients[2], "wait-rejected-123456")
        assert retried.status_code == 202
        assert clients[2].operation("wait-rejected-123456") is not None


def test_history_never_rejects_immediate_slot_or_idempotent_replay(queue_db, controlled_queue):
    history(queue_db, [500_000])
    controlled_queue.settings.chat_queue_timeout_seconds = 180
    with authenticated_client(queue_db) as client:
        accepted = submit(client, "wait-immediate-123456")
        assert accepted.status_code == 202 and accepted.json()["status"] == "running"
        replay = submit(client, "wait-immediate-123456")
        assert replay.status_code == 202
        assert replay.json()["operation_id"] == accepted.json()["operation_id"]
        assert len(controlled_queue.launches) == 1


def test_cold_start_does_not_invent_latency(queue_db, controlled_queue):
    controlled_queue.settings.chat_queue_max_size = 50
    with ExitStack() as stack:
        clients = [stack.enter_context(authenticated_client(queue_db)) for _ in range(3)]
        for _ in range(2):
            stack.enter_context(admission("chat", 2))
        for index, client in enumerate(clients):
            accepted = submit(client, f"wait-unknown-{index}-123456")
            assert accepted.status_code == 202 and accepted.json()["status"] == "queued"
        with queue_db() as db:
            assert chat_queue._estimated_queue_wait_ms(db, {"active": 2, "queued": 3, "limit": 2}) is None


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired"])
def test_short_failures_do_not_lower_completed_duration_estimate(queue_db, status):
    history(queue_db, [200_000])
    history(queue_db, [1] * 25, status=status)
    with queue_db() as db:
        assert chat_queue._estimated_queue_wait_ms(db, {"active": 2, "queued": 0, "limit": 2}) == 200_000


@pytest.mark.parametrize("duration", [None, 0, -1])
def test_unknown_or_invalid_completed_duration_does_not_predict_wait(queue_db, duration):
    history(queue_db, [duration])
    with queue_db() as db:
        assert chat_queue._estimated_queue_wait_ms(db, {"active": 2, "queued": 50, "limit": 2}) is None


@pytest.mark.parametrize(("active", "queued", "expected"), [(2, 0, 60_000), (2, 1, 60_000), (2, 2, 120_000), (0, 1, 0)])
def test_fifo_estimate_uses_effective_parallel_capacity(queue_db, active, queued, expected):
    history(queue_db, [60_000])
    with queue_db() as db:
        assert chat_queue._estimated_queue_wait_ms(db, {"active": active, "queued": queued, "limit": 2}) == expected


def test_prediction_reuses_bounded_metrics_query_and_refreshes_after_ttl(queue_db, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(chat_queue, "monotonic", lambda: clock[0])
    history(queue_db, [60_000])
    capacity = {"active": 2, "queued": 0, "limit": 2}
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = queue_db.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with queue_db() as db:
            public = chat_queue.terminal_metrics(db)
            assert chat_queue._estimated_queue_wait_ms(db, capacity) == 60_000
            assert len(statements) == 1
            assert "completed_ms" not in public
        history(queue_db, [200_000])
        statements.clear()
        with queue_db() as db:
            assert chat_queue._estimated_queue_wait_ms(db, capacity) == 60_000
            assert not statements
            clock[0] += 2
            assert chat_queue._estimated_queue_wait_ms(db, capacity) == 200_000
            assert len(statements) == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_cache_reset_between_snapshot_and_prediction_keeps_unknown_estimate(queue_db, monkeypatch):
    history(queue_db, [200_000])
    original = chat_queue.terminal_metrics

    def reset_after_read(db):
        result = original(db)
        with chat_queue._metrics_lock:
            chat_queue._metrics_cache.clear()
        return result

    monkeypatch.setattr(chat_queue, "terminal_metrics", reset_after_read)
    with queue_db() as db:
        assert chat_queue._estimated_queue_wait_ms(db, {"active": 2, "queued": 0, "limit": 2}) is None
