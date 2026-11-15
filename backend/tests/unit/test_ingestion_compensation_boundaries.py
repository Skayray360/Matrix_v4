# Creado por Aldo Garcia.
"""Fallo SQL y reindexacion privada no abandonan generaciones vectoriales."""

import pytest
from sqlalchemy import event, select

from app.common.errors import IndexUpdateRequiredError, IngestionFailedError
from app.database.models import DocumentVersion, IngestionJob
from app.ingestion.pending_generation_cleanup import CLEANUP_JOB_TYPE, retry_pending_generation_cleanup
from app.ingestion.reconciler import ReconcileStats, _reconcile_private_documents
from app.rag.retriever import Retriever
from tests.conftest import make_context
from tests.unit import test_private_reindex_final as private_fixtures

pytestmark = pytest.mark.unit
environment = private_fixtures.environment


def test_failed_sql_version_flush_removes_unpublished_vectors(environment):
    env = environment
    document, _ = private_fixtures.legacy_attachment(env)
    previous_generation = document.active_generation
    collection = env.store.collection_for(document.scope)

    def fail_version(*_args):
        raise RuntimeError("synthetic SQL version failure")

    event.listen(DocumentVersion, "before_insert", fail_version)
    try:
        with pytest.raises(RuntimeError, match="synthetic SQL version failure"):
            env.service.reindex_conversation_attachment(env.db, document=document, force=True)
    finally:
        event.remove(DocumentVersion, "before_insert", fail_version)
        env.db.rollback()

    points, _ = env.store._client.scroll(collection_name=collection, limit=10)
    assert len(points) == 1
    assert points[0].payload["generation"] == previous_generation
    assert document.active_generation == previous_generation


def test_private_reindex_preserves_durable_cleanup_and_retries(environment, monkeypatch):
    env = environment
    document, _ = private_fixtures.legacy_attachment(env)
    previous_generation = document.active_generation
    collection = env.store.collection_for(document.scope)
    original_upsert = env.store.upsert_chunks

    def partial_write(chunks, vectors):
        original_upsert(chunks, vectors)
        raise RuntimeError("synthetic partial vector write")

    def unavailable_cleanup(*_args, **_kwargs):
        raise OSError("synthetic Qdrant outage")

    with monkeypatch.context() as patcher:
        patcher.setattr(env.store, "upsert_chunks", partial_write)
        patcher.setattr(env.store, "delete_generation", unavailable_cleanup)
        stats = ReconcileStats()
        _reconcile_private_documents(env.db, ingestion=env.service, stats=stats, force=True)

    job = env.db.scalar(select(IngestionJob).where(IngestionJob.job_type == CLEANUP_JOB_TYPE))
    assert job is not None and job.status == "pending"
    assert job.stats["document_id"] == document.id
    assert job.stats["scope"] == "conversation"
    assert job.stats["generation"] != previous_generation
    assert stats.failures == [{"path": f"private:{document.id}", "error": "GenerationCleanupPendingError"}]
    assert document.active_generation == previous_generation
    assert env.store.count(collection) == 2

    monkeypatch.setattr("app.rag.vector_store.get_vector_store", lambda: env.store)
    assert retry_pending_generation_cleanup(env.db) == {"completed": 1, "failed": 0, "preserved_active": 0}
    points, _ = env.store._client.scroll(collection_name=collection, limit=10)
    assert len(points) == 1 and points[0].payload["generation"] == previous_generation
    assert job.status == "completed"


def test_reindex_does_not_publish_embeddings_under_an_obsolete_revision(environment, monkeypatch):
    env = environment
    document, _ = private_fixtures.legacy_attachment(env)
    previous_generation = document.active_generation
    original_embed = env.llm.embed
    revision = "initial-revision"
    monkeypatch.setattr(env.llm, "embedding_revision", lambda: revision, raising=False)

    def changed_runtime(texts):
        nonlocal revision
        vectors = original_embed(texts)
        revision = "replacement-revision"
        return vectors

    monkeypatch.setattr(env.llm, "embed", changed_runtime)
    with pytest.raises(IngestionFailedError, match="configuracion cambio"):
        env.service.reindex_conversation_attachment(env.db, document=document, force=True)
    env.db.rollback()
    assert document.active_generation == previous_generation
    assert env.store.count(env.store.collection_for(document.scope)) == 1


def test_query_revision_change_never_reaches_vector_ranking(environment, monkeypatch):
    env = environment
    revision = "initial-revision"
    monkeypatch.setattr(env.llm, "embedding_revision", lambda: revision, raising=False)
    document, _ = private_fixtures.legacy_attachment(env)
    env.service.reindex_conversation_attachment(env.db, document=document, force=True)
    env.db.commit()
    original_embed = env.llm.embed

    def changed_runtime(texts):
        nonlocal revision
        vectors = original_embed(texts)
        revision = "replacement-revision"
        return vectors

    monkeypatch.setattr(env.llm, "embed", changed_runtime)
    monkeypatch.setattr(env.store, "search", lambda **_kwargs: pytest.fail("Ranking across embedding revisions"))
    with pytest.raises(IndexUpdateRequiredError):
        Retriever(store=env.store, llm=env.llm).retrieve(
            ctx=make_context(user_id="user-a"), question="politica.md", authorized_categories=frozenset(),
            conversation_id="chat-a",
        )
