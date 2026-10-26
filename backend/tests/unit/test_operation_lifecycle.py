# Creado por Aldo Garcia.
"""Measured terminal lifecycle, bounded aggregates and isolated import regressions."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from app.common import operation_lifecycle as lifecycle
from app.database.models import ChatOperation

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 26, 12, 0, 0, 500000)


@pytest.fixture
def operations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'operations.db'}")
    ChatOperation.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def operation(key="synthetic-operation", **fields):
    values = {
        "id": key, "user_id": "synthetic-user", "conversation_id": "synthetic-conversation",
        "request_hash": "a" * 64, "authorization_scope": "b" * 64, "status": "running",
        "created_at": NOW - timedelta(seconds=5), "started_at": NOW - timedelta(seconds=3),
        "message": "PRIVATE_SYNTHETIC_PROMPT", "session_id": "PRIVATE_SYNTHETIC_SESSION",
    }
    values.update(fields)
    return ChatOperation(**values)


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_transition_records_times_once_and_drops_temporary_payload(operations, monkeypatch, status):
    monkeypatch.setattr(lifecycle, "utcnow_naive", lambda: NOW)
    with operations() as db:
        row = operation()
        db.add(row)
        db.commit()
        assert lifecycle.finalize_operations(db, ChatOperation.id == row.id, status=status) == 1
        db.commit()
        assert (row.queue_ms, row.execution_ms, row.total_ms) == (2000, 3000, 5000)
        assert row.finished_at == NOW and row.status == status
        assert row.message is row.session_id is None
        monkeypatch.setattr(lifecycle, "utcnow_naive", lambda: NOW + timedelta(days=1))
        assert lifecycle.finalize_operations(db, ChatOperation.id == row.id, status="cancelled") == 0
        db.commit()
        assert row.finished_at == NOW and row.status == status
        assert row.total_ms == 5000


def test_recovery_records_observation_but_never_fabricates_execution_end(operations, monkeypatch):
    monkeypatch.setattr(lifecycle, "utcnow_naive", lambda: NOW)
    with operations() as db:
        running = operation("running")
        legacy_running = operation("legacy-running", started_at=None)
        queued = operation("queued", status="queued", started_at=None)
        historical = operation("historical", status="completed", started_at=None, message=None, session_id=None)
        db.add_all([running, legacy_running, queued, historical])
        db.commit()
        assert lifecycle.finalize_operations(db, status="expired", execution_end_known=False) == 3
        db.commit()
        assert running.finished_at == queued.finished_at == NOW
        assert (running.queue_ms, running.execution_ms, running.total_ms) == (2000, None, None)
        assert legacy_running.finished_at == NOW
        assert legacy_running.queue_ms is legacy_running.execution_ms is legacy_running.total_ms is None
        assert (queued.queue_ms, queued.execution_ms, queued.total_ms) == (5000, None, 5000)
        assert historical.status == "completed"
        assert historical.finished_at is historical.total_ms is None


def test_terminal_write_rolls_back_with_the_callers_transaction(operations, monkeypatch):
    monkeypatch.setattr(lifecycle, "utcnow_naive", lambda: NOW)
    with operations() as db:
        row = operation()
        db.add(row)
        db.commit()
        lifecycle.finalize_operations(db, status="completed", response={"answer": "synthetic"})
        db.rollback()
        assert row.status == "running" and row.response is None
        assert row.finished_at is row.total_ms is None
        assert row.message == "PRIVATE_SYNTHETIC_PROMPT"


def test_missing_or_reversed_clock_stays_unknown(operations, monkeypatch):
    monkeypatch.setattr(lifecycle, "utcnow_naive", lambda: NOW)
    assert lifecycle.elapsed_ms(None, NOW) is None
    assert lifecycle.elapsed_ms(NOW, NOW - timedelta(microseconds=1)) is None
    with operations() as db:
        row = operation(created_at=NOW + timedelta(seconds=2), started_at=NOW + timedelta(seconds=3))
        db.add(row)
        db.commit()
        lifecycle.finalize_operations(db, status="failed")
        db.commit()
        assert row.queue_ms == 1000  # That interval alone is known.
        assert row.execution_ms is row.total_ms is None


def test_metrics_are_bounded_cached_and_do_not_select_or_emit_private_data(operations, monkeypatch):
    from app.agents import chat_queue

    clock = [100.0]
    monkeypatch.setattr(chat_queue, "monotonic", lambda: clock[0])
    with operations() as db:
        db.add_all([
            operation(
                str(index), status="completed", finished_at=NOW + timedelta(seconds=index),
                queue_ms=index, execution_ms=None if index % 2 else index * 2,
                total_ms=None if index % 2 else index * 3,
            ) for index in range(1010)
        ])
        db.add(operation("historical", status="failed", finished_at=None))
        db.commit()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            metrics = chat_queue.terminal_metrics(db)
            assert chat_queue.terminal_metrics(db) == metrics
            assert len(statements) == 1
            clock[0] += 2
            assert chat_queue.terminal_metrics(db) == metrics
            assert len(statements) == 2
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
        assert metrics["sample_size"] == metrics["sample_limit"] == 1000
        assert metrics["terminal_counts"] == {"completed": 1000, "failed": 0, "cancelled": 0, "expired": 0}
        assert metrics["queue_ms"] == {"samples": 1000, "p50": 509, "p95": 959, "p99": 999}
        assert metrics["execution_ms"] == {"samples": 500, "p50": 1016, "p95": 1916, "p99": 1996}
        serialized = json.dumps(metrics)
        assert "PRIVATE" not in serialized and "synthetic-user" not in serialized
        for statement in statements:
            assert all(name not in statement for name in ("chat_operations.message", "chat_operations.response", "chat_operations.user_id", "chat_operations.session_id"))


def test_empty_or_unknown_metrics_are_null_not_zero(operations):
    from app.agents.chat_queue import terminal_metrics

    with operations() as db:
        db.add(operation(status="expired", finished_at=NOW))
        db.commit()
        metrics = terminal_metrics(db)
        assert metrics["sample_size"] == 1 and metrics["terminal_counts"]["expired"] == 1
        for name in ("queue_ms", "execution_ms", "total_ms"):
            assert metrics[name] == {"samples": 0, "p50": None, "p95": None, "p99": None}


def test_mysql_ddl_keeps_millisecond_measurements_and_nullable_history():
    from app.database.migrator import discover_migrations, split_statements

    ddl = str(CreateTable(ChatOperation.__table__).compile(dialect=mysql.dialect()))
    for name in ("created_at", "started_at", "finished_at"):
        assert f"{name} DATETIME(6)" in ddl
    migration = next(item for item in discover_migrations() if item.version == "0013")
    assert len(split_statements(migration.sql)) == 7
    assert "UPDATE chat_operations" not in migration.sql
    assert "ADD COLUMN finished_at DATETIME(6) NULL" in migration.sql


@pytest.mark.parametrize("module", ["app.memory.service", "app.ingestion.service"])
def test_neutral_lifecycle_helper_does_not_create_an_import_cycle(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
