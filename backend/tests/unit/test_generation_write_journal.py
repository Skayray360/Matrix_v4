# Creado por Aldo Garcia.
"""Recuperacion durable real: transacciones SQLite y Qdrant aislados."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.common.errors import QdrantUnavailableError
from app.database.models import Document
from app.ingestion.pending_generation_cleanup import retry_generation_write_intents
from app.rag.vector_store import VectorStore
from tests.unit import test_private_reindex_final as private_fixtures

pytestmark = pytest.mark.unit
environment = private_fixtures.environment


def write_candidate(env, *, existing=False):
    if existing:
        document, _ = private_fixtures.legacy_attachment(env)
        result = env.service.reindex_conversation_attachment(env.db, document=document, force=True)
    else:
        result = env.service.ingest_conversation_attachment(
            env.db, data=b"Synthetic retention policy.", display_name="synthetic.md",
            internal_filename="synthetic.md", mime_type="text/markdown", owner_user_id="user-a",
            conversation_id="chat-a",
        )
    return result.document_id


def recover(env):
    with env.factory() as recovery:
        return retry_generation_write_intents(recovery, store=env.store)


@pytest.mark.parametrize("existing", [False, True])
def test_caller_commit_failure_leaves_durable_intent_even_without_document(environment, existing):
    env = environment
    document_id = write_candidate(env, existing=existing)

    def failed_commit(_session):
        raise RuntimeError("synthetic commit failure")

    event.listen(env.db, "before_commit", failed_commit)
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            env.db.commit()
    finally:
        event.remove(env.db, "before_commit", failed_commit)
        env.db.rollback()
    assert len(env.store.generation_journal.pending()) == 1
    with env.factory() as observer:
        document = observer.get(Document, document_id)
        assert (document is not None) is existing
        if existing:
            assert document.active_generation == "legacy-attachment"
    assert recover(env) == {"completed": 1, "failed": 0, "preserved_active": 0}
    assert env.store.count(env.store.collection_for("conversation")) == int(existing)
    assert not env.store.generation_journal.pending()


def test_ambiguous_commit_response_preserves_generation_that_sql_really_committed(environment):
    env = environment
    document_id = write_candidate(env)

    def response_lost(_session):
        raise RuntimeError("synthetic lost commit response")

    event.listen(env.db, "after_commit", response_lost)
    try:
        with pytest.raises(RuntimeError, match="lost commit response"):
            env.db.commit()
    finally:
        event.remove(env.db, "after_commit", response_lost)
        env.db.close()
    with env.factory() as observer:
        assert observer.get(Document, document_id).active_generation
    assert recover(env) == {"completed": 1, "failed": 0, "preserved_active": 1}
    assert env.store.count(env.store.collection_for("conversation")) == 1
    assert not env.store.generation_journal.pending()


@pytest.mark.parametrize("failure", ["delete_generation", "discard"])
def test_failed_vector_or_journal_cleanup_keeps_retryable_intent(environment, monkeypatch, failure):
    env = environment
    write_candidate(env)
    env.db.rollback()
    target = env.store if failure == "delete_generation" else env.store.generation_journal
    with monkeypatch.context() as patcher:
        patcher.setattr(target, failure, Mock(side_effect=OSError("synthetic cleanup failure")))
        assert recover(env) == {"completed": 0, "failed": 1, "preserved_active": 0}
    assert len(env.store.generation_journal.pending()) == 1
    assert recover(env) == {"completed": 1, "failed": 0, "preserved_active": 0}
    assert env.store.count(env.store.collection_for("conversation")) == 0


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unconfirmed_intent_never_reaches_chunk_upsert(environment, monkeypatch, ambiguous):
    env = environment
    original = env.store.generation_journal.record
    chunks = Mock(side_effect=AssertionError("chunks without durable intent"))

    def failed_intent(intent):
        if ambiguous:
            original(intent)
        raise QdrantUnavailableError("synthetic journal response failure")

    monkeypatch.setattr(env.store.generation_journal, "record", failed_intent)
    monkeypatch.setattr(env.store, "upsert_chunks", chunks)
    with pytest.raises(QdrantUnavailableError):
        write_candidate(env)
    env.db.rollback()
    chunks.assert_not_called()
    assert len(env.store.generation_journal.pending()) == int(ambiguous)
    assert recover(env)["completed"] == int(ambiguous)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("commit", [False, True])
def test_live_writer_excludes_cleanup_until_caller_transaction_resolves(environment, existing, commit):
    env = environment
    write_candidate(env, existing=existing)
    assert recover(env) == {"completed": 0, "failed": 1, "preserved_active": 0}
    assert env.store.count(env.store.collection_for("conversation")) == 1 + int(existing)
    assert len(env.store.generation_journal.pending()) == 1
    if commit:
        env.db.commit()
    else:
        env.db.rollback()
    assert recover(env) == {"completed": 1, "failed": 0, "preserved_active": int(commit)}
    assert env.store.count(env.store.collection_for("conversation")) == int(commit) + int(existing)


def test_same_session_cannot_retire_intent_before_its_own_commit(environment):
    env = environment
    write_candidate(env)
    with pytest.raises(ValueError, match="resolver primero"):
        retry_generation_write_intents(env.db, store=env.store)
    env.db.rollback()
    assert recover(env)["completed"] == 1


def test_scope_mismatch_is_not_inferred_to_be_an_orphan(environment):
    env = environment
    document_id = write_candidate(env)
    env.db.commit()
    env.db.get(Document, document_id).scope = "corporate"
    env.db.commit()
    assert recover(env) == {"completed": 0, "failed": 1, "preserved_active": 0}
    assert len(env.store.generation_journal.pending()) == 1
    assert env.store.count(env.store.collection_for("conversation")) == 1


def test_acknowledged_delete_does_not_discard_intent(environment, monkeypatch):
    env = environment
    write_candidate(env)
    env.db.rollback()
    original = env.store._client.delete

    def acknowledged(*args, **kwargs):
        if kwargs.get("collection_name") == env.store.collection_for("conversation"):
            return models.UpdateResult(operation_id=0, status=models.UpdateStatus.ACKNOWLEDGED)
        return original(*args, **kwargs)

    monkeypatch.setattr(env.store._client, "delete", acknowledged)
    assert recover(env) == {"completed": 0, "failed": 1, "preserved_active": 0}
    assert len(env.store.generation_journal.pending()) == 1
    assert env.store.count(env.store.collection_for("conversation")) == 1


def test_acknowledged_upsert_does_not_publish_sql_manifest(environment, monkeypatch):
    env = environment
    original = env.store._client.upsert

    def acknowledged(*args, **kwargs):
        if kwargs.get("collection_name") == env.store.collection_for("conversation"):
            return models.UpdateResult(operation_id=0, status=models.UpdateStatus.ACKNOWLEDGED)
        return original(*args, **kwargs)

    monkeypatch.setattr(env.store._client, "upsert", acknowledged)
    with pytest.raises(QdrantUnavailableError):
        write_candidate(env)
    env.db.rollback()
    assert env.db.scalar(select(Document.id)) is None
    env.db.rollback()
    assert recover(env)["completed"] == 1


@pytest.mark.parametrize("existing", [False, True])
def test_abrupt_process_exit_recovers_persistent_candidate_preserving_previous_manifest(environment, existing):
    env = environment
    manifest = env.root / "crashed-manifest.db"
    vectors = env.root / "crashed-qdrant"
    code = r'''
import os
import sys
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.config.settings import Settings
from app.database.models import Document, DocumentVersion
from app.ingestion import service, loaders
from app.rag import vector_store, index_manifest
settings = Settings(_env_file=None, rag_embedding_dimension=int(sys.argv[3]))
service.get_settings = vector_store.get_settings = index_manifest.get_settings = lambda: settings
service.extract_document = loaders.extract_document
engine = create_engine('sqlite:///' + sys.argv[1])
Document.__table__.create(engine)
DocumentVersion.__table__.create(engine)
db = Session(engine)
document = Document(id='crashed-document', scope='conversation', filename='synthetic.md',
                    mime_type='text/markdown', sha256='a' * 64, owner_user_id='synthetic-owner',
                    conversation_id='synthetic-conversation')
db.add(document)
db.flush()
store = vector_store.VectorStore(client=QdrantClient(path=sys.argv[2]))
class LocalDouble:
    def embed(self, texts):
        return [[1.0] + [0.0] * (settings.rag_embedding_dimension - 1) for text in texts]
ingestion = service.IngestionService(store=store, llm=LocalDouble())
if sys.argv[4] == 'existing':
    ingestion._index_document(db, document=document, data=b'Synthetic previous policy.', category=None)
    db.commit()
    from app.ingestion.pending_generation_cleanup import retry_generation_write_intents
    retry_generation_write_intents(db, store=store)
ingestion._index_document(db, document=document, data=b'Synthetic crash policy.', category=None)
os._exit(17)
'''
    completed = subprocess.run(
        [sys.executable, "-c", code, str(manifest), str(vectors), str(env.settings.rag_embedding_dimension),
         "existing" if existing else "new"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 17, completed.stderr
    engine = create_engine(f"sqlite:///{manifest}")
    store = VectorStore(client=QdrantClient(path=str(vectors)))
    try:
        assert len(store.generation_journal.pending()) == 1
        assert store.count(store.collection_for("conversation")) == 1 + int(existing)
        with Session(engine) as db:
            document = db.get(Document, "crashed-document")
            assert (document is not None) is existing
            previous = document.active_generation if document is not None else None
            assert retry_generation_write_intents(db, store=store) == {
                "completed": 1, "failed": 0, "preserved_active": 0,
            }
        assert store.count(store.collection_for("conversation")) == int(existing)
        if existing:
            points, _ = store._client.scroll(collection_name=store.collection_for("conversation"), limit=8)
            assert points[0].payload["generation"] == previous
        assert not store.generation_journal.pending()
    finally:
        store.close()
        engine.dispose()
