# Creado por Aldo Garcia.
"""Un indice obsoleto no se confunde con ausencia de evidencia ni revela otro alcance."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient, models

from app.common.chat_failures import chat_failure_code
from app.common.errors import IndexUpdateRequiredError
from app.common.ids import utcnow_naive
from app.database.models import Document
from app.rag.index_manifest import active_filter, indexing_fingerprint
from app.rag.retriever import Retriever
from app.rag.vector_store import VectorStore
from tests.unit import test_private_reindex_final as private_fixtures

pytestmark = pytest.mark.unit
environment = private_fixtures.environment


def publish_manifest(factory, identifier, *, category="prestaciones", fingerprint="legacy", scope="corporate",
                     owner=None, conversation=None, status="indexed", deleted=False):
    with factory() as db:
        db.add(Document(id=identifier, scope=scope, category=category, filename=f"{identifier}.md",
                        mime_type="text/markdown", sha256="a" * 64, status=status, chunk_count=1,
                        active_generation=f"generation-{identifier}", index_fingerprint=fingerprint,
                        owner_user_id=owner, conversation_id=conversation,
                        deleted_at=utcnow_naive() if deleted else None))
        db.commit()


@pytest.mark.parametrize("summary", [False, True])
def test_stale_authorized_document_blocks_before_embedding_or_content_retrieval(manifest_db, restricted_context, summary):
    publish_manifest(manifest_db, "old")
    store = VectorStore(client=QdrantClient(location=":memory:"))
    llm = SimpleNamespace(embed_one=lambda text: pytest.fail("No embedding antes de actualizar el indice"))
    try:
        with pytest.raises(IndexUpdateRequiredError) as error:
            Retriever(store=store, llm=llm).retrieve(
                ctx=restricted_context, question="Resume el documento" if summary else "Que requisito aplica?",
                authorized_categories=frozenset({"prestaciones"}), include_private=False, summary=summary,
            )
        assert store._client.get_collections().collections == []
        assert chat_failure_code(error.value) == "index_update_required"
        assert error.value.status_code == 503 and "old" not in error.value.message
    finally:
        store.close()


def test_denied_category_or_private_owner_never_blocks_or_leaks_into_catalog(manifest_db):
    fingerprint = indexing_fingerprint()
    publish_manifest(manifest_db, "allowed", fingerprint=fingerprint)
    publish_manifest(manifest_db, "forbidden", category="nomina")
    publish_manifest(manifest_db, "other-owner", scope="conversation", category=None, owner="other", conversation="c")
    publish_manifest(manifest_db, "other-chat", scope="conversation", category=None, owner="me", conversation="other")
    store = VectorStore(client=QdrantClient(location=":memory:"))
    try:
        rows = store.list_authorized_documents(scope="corporate", authorized_categories=frozenset({"prestaciones"}),
                                               index_fingerprint=fingerprint)
        assert [row.document_id for row in rows] == ["allowed"]
        assert not store.list_authorized_documents(scope="corporate", index_fingerprint=fingerprint)
        assert not store.list_authorized_documents(scope="conversation", user_id="me", conversation_id="c",
                                                   index_fingerprint=fingerprint)
        query = store.build_corporate_filter(frozenset({"prestaciones"}))
        query.must.append(models.FieldCondition(key="index_fingerprint", match=models.MatchValue(value=fingerprint)))
        active_filter(query)
    finally:
        store.close()


@pytest.mark.parametrize("status,deleted", [("pending", False), ("failed", False), ("indexed", True)])
def test_unpublished_or_deleted_documents_do_not_require_index_update(manifest_db, status, deleted):
    publish_manifest(manifest_db, "unavailable", status=status, deleted=deleted)
    store = VectorStore(client=QdrantClient(location=":memory:"))
    try:
        assert not store.list_authorized_documents(scope="corporate", authorized_categories=frozenset({"prestaciones"}),
                                                   index_fingerprint=indexing_fingerprint())
    finally:
        store.close()


def test_mixed_current_and_stale_scope_is_blocked_in_catalog_and_vector_filter(manifest_db):
    fingerprint = indexing_fingerprint()
    publish_manifest(manifest_db, "current", fingerprint=fingerprint)
    publish_manifest(manifest_db, "old")
    store = VectorStore(client=QdrantClient(location=":memory:"))
    try:
        with pytest.raises(IndexUpdateRequiredError):
            store.list_authorized_documents(scope="corporate", authorized_categories=frozenset({"prestaciones"}),
                                            index_fingerprint=fingerprint)
        query = store.build_corporate_filter(frozenset({"prestaciones"}))
        query.must.append(models.FieldCondition(key="index_fingerprint", match=models.MatchValue(value=fingerprint)))
        with pytest.raises(IndexUpdateRequiredError):
            active_filter(query)
        with manifest_db() as db:
            assert db.get(Document, "old").index_fingerprint == "legacy"
    finally:
        store.close()


def test_reindex_unchanged_private_source_requires_commit_then_clears_guard(environment):
    env = environment
    document, stored = private_fixtures.legacy_attachment(env)
    original = stored.read_bytes()
    with pytest.raises(IndexUpdateRequiredError):
        private_fixtures.summary(env)
    outcome = env.service.reindex_conversation_attachment(env.db, document=document)
    assert not outcome.skipped
    with pytest.raises(IndexUpdateRequiredError):
        private_fixtures.summary(env)
    env.db.commit()
    assert stored.read_bytes() == original
    result = private_fixtures.summary(env)
    assert result.evidences and "no tiene derecho al bono" in result.evidences[0].text
    assert document.index_fingerprint == indexing_fingerprint(env.llm)
