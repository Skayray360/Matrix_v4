# Creado por Aldo Garcia.
"""Locks reales entre sesiones SQLite y contrato de current-read para MySQL."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.database.document_locks import lock_document
from app.database.models import Document

pytestmark = pytest.mark.unit
ORIGINAL_TIMESTAMP = datetime(2026, 1, 2, 3, 4, 5)


def document(document_id="doc"):
    return Document(
        id=document_id,
        scope="corporate",
        filename="fixture.txt",
        mime_type="text/plain",
        sha256="a" * 64,
        status="indexed",
        active_generation="original",
        updated_at=ORIGINAL_TIMESTAMP,
    )


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'locks.sqlite'}", connect_args={"timeout": 5})
    Document.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(document())
    yield factory
    engine.dispose()


def test_lock_preserves_pending_caller_changes_without_committing(sessions):
    with sessions() as writer, sessions() as observer:
        original = writer.get(Document, "doc")
        original.storage_path = "private/portable.txt"
        original.ingestion_version = "next-version"
        locked = lock_document(writer, "doc")

        assert locked is original
        assert locked.storage_path == "private/portable.txt"
        assert locked.ingestion_version == "next-version"
        assert observer.get(Document, "doc").storage_path is None
        writer.rollback()
        observer.expire_all()
        assert observer.get(Document, "doc").ingestion_version == "1"


def test_lock_refreshes_stale_identity_map_and_keeps_unindexed_generation(sessions):
    with sessions() as reader:
        stale = reader.get(Document, "doc")
        with sessions.begin() as updater:
            changed = updater.get(Document, "doc")
            changed.active_generation = "published"
            changed.status = "error"
            changed.deleted_at = ORIGINAL_TIMESTAMP
        assert stale.active_generation == "original"
        locked = lock_document(reader, "doc")
        assert locked is stale
        assert locked.active_generation == "published"
        assert locked.status == "error"
        assert locked.deleted_at == ORIGINAL_TIMESTAMP


def test_lock_does_not_change_updated_at(sessions):
    with sessions.begin() as db:
        assert lock_document(db, "doc").updated_at == ORIGINAL_TIMESTAMP
    with sessions() as db:
        assert db.get(Document, "doc").updated_at == ORIGINAL_TIMESTAMP


def test_nowait_defers_while_writer_owns_document_and_restores_connection_timeout(sessions):
    with sessions() as writer, sessions() as cleaner:
        lock_document(writer, "doc")
        connection = cleaner.connection()
        initial_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()

        with pytest.raises(OperationalError, match="locked"):
            lock_document(cleaner, "doc", wait=False)

        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == initial_timeout
        cleaner.rollback()
        writer.rollback()
        assert lock_document(cleaner, "doc", wait=False).id == "doc"


def test_uncommitted_new_document_is_never_reported_as_absent(sessions):
    with sessions() as writer, sessions() as cleaner:
        writer.add(document("new"))
        assert lock_document(writer, "new").id == "new"

        with pytest.raises(OperationalError, match="locked"):
            lock_document(cleaner, "new", wait=False)

        cleaner.rollback()
        writer.rollback()  # Simulates the SQL rollback after an indexer crash.
        assert lock_document(cleaner, "new", wait=False) is None


def test_absent_document_still_acquires_lock_until_caller_ends_transaction(sessions):
    with sessions() as first_cleaner, sessions() as other_cleaner:
        assert lock_document(first_cleaner, "missing", wait=False) is None
        with pytest.raises(OperationalError, match="locked"):
            lock_document(other_cleaner, "missing", wait=False)
        other_cleaner.rollback()
        first_cleaner.rollback()
        assert lock_document(other_cleaner, "missing", wait=False) is None


@pytest.mark.parametrize("wait", [False, True])
def test_mysql_lock_is_current_read_by_primary_key_without_skipping_locked_rows(wait):
    db = Mock(spec=Session)
    db.get_bind.return_value = SimpleNamespace(dialect=mysql.dialect())
    db.execute.return_value.scalar_one_or_none.return_value = None

    assert lock_document(db, "doc", wait=wait) is None

    db.flush.assert_called_once_with()
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect()))
    assert "WHERE documents.id = %s FOR UPDATE" in sql
    assert ("NOWAIT" in sql) is (not wait)
    assert "SKIP LOCKED" not in sql
    assert statement.get_execution_options()["populate_existing"] is True
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
