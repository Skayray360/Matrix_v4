# Creado por Aldo Garcia.
"""Mantenimiento optativo de vectores anteriores al journal, sin texto real."""

import json
from dataclasses import replace
from uuid import UUID

import pytest
from qdrant_client import grpc
from sqlalchemy.exc import OperationalError

from app.common.ids import new_id
from app.ingestion.legacy_vector_cleanup import review_legacy_generations
from tests.unit import test_private_reindex_final as private_fixtures

pytestmark = pytest.mark.unit
environment = private_fixtures.environment


def legacy_point(env, *, document_id="missing-document", generation="legacy-orphan"):
    chunk = private_fixtures.make_chunk(
        "Synthetic metadata cleanup fixture.", category="__private__", scope="conversation",
        document_id=document_id, owner="user-a", conversation="chat-a",
    )
    chunk = replace(chunk, metadata=replace(
        chunk.metadata, chunk_id=new_id(), generation=generation, index_fingerprint="a" * 64,
    ))
    env.store.upsert_chunks([chunk], [[1.0] + [0.0] * (env.settings.rag_embedding_dimension - 1)])
    return chunk


def test_preview_never_deletes_and_apply_preserves_active_and_removes_manifestless_generations(environment, monkeypatch):
    env = environment
    document, _ = private_fixtures.legacy_attachment(env)
    legacy_point(env, document_id=document.id, generation="obsolete-generation")
    legacy_point(env)
    original = env.store._client.scroll

    def metadata_only(*args, **kwargs):
        assert kwargs["with_vectors"] is False
        assert kwargs["with_payload"].include == ["document_id", "generation", "scope"]
        return original(*args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(env.store._client, "scroll", metadata_only)
        preview = review_legacy_generations(env.db, store=env.store, scope="conversation")
        assert preview["candidate_generations"] == 2 and preview["preserved_active"] == 1
        assert preview["deleted_generations"] == 0
        assert env.store.count(env.store.collection_for("conversation")) == 3
        applied = review_legacy_generations(
            env.db, store=env.store, scope="conversation", apply=True, expected_manifest_count=1,
        )
    assert applied["deleted_generations"] == 2 and applied["preserved_active"] == 1
    points, _ = env.store._client.scroll(collection_name=env.store.collection_for("conversation"), limit=8)
    assert len(points) == 1 and points[0].payload["generation"] == "legacy-attachment"


def test_empty_or_unexpected_sql_manifest_requires_explicit_operator_choice(environment):
    env = environment
    legacy_point(env)
    preview = review_legacy_generations(env.db, store=env.store, scope="conversation")
    assert preview["manifest_count"] == 0 and preview["candidate_generations"] == 1
    with pytest.raises(ValueError, match="conteo SQL"):
        review_legacy_generations(env.db, store=env.store, scope="conversation", apply=True,
                                  expected_manifest_count=1)
    env.db.rollback()
    with pytest.raises(ValueError, match="manifest SQL vacio"):
        review_legacy_generations(env.db, store=env.store, scope="conversation", apply=True,
                                  expected_manifest_count=0)
    env.db.rollback()
    assert env.store.count(env.store.collection_for("conversation")) == 1
    assert review_legacy_generations(
        env.db, store=env.store, scope="conversation", apply=True,
        expected_manifest_count=0, allow_empty_manifest=True,
    )["deleted_generations"] == 1


def test_sql_failure_never_authorizes_deletion(environment, monkeypatch):
    env = environment
    private_fixtures.legacy_attachment(env)
    legacy_point(env)

    def unavailable(*_args, **_kwargs):
        raise OperationalError("synthetic SQL outage", {}, RuntimeError("unavailable"))

    monkeypatch.setattr("app.ingestion.legacy_vector_cleanup.lock_document", unavailable)
    with pytest.raises(OperationalError):
        review_legacy_generations(env.db, store=env.store, scope="conversation", apply=True,
                                  expected_manifest_count=1)
    assert env.store.count(env.store.collection_for("conversation")) == 2


def test_live_writer_is_not_a_legacy_orphan(environment):
    env = environment
    document, _ = private_fixtures.legacy_attachment(env)
    env.service.reindex_conversation_attachment(env.db, document=document, force=True)
    with env.factory() as cleaner, pytest.raises(OperationalError):
        review_legacy_generations(cleaner, store=env.store, scope="conversation", apply=True,
                                  expected_manifest_count=1)
    assert env.store.count(env.store.collection_for("conversation")) == 2
    env.db.rollback()


def test_inconsistent_scope_metadata_is_reported_without_deleting(environment):
    env = environment
    private_fixtures.legacy_attachment(env)
    chunk = legacy_point(env)
    env.store._client.set_payload(
        collection_name=env.store.collection_for("conversation"), points=[chunk.metadata.chunk_id],
        payload={"scope": "corporate"},
    )
    result = review_legacy_generations(env.db, store=env.store, scope="conversation", apply=True,
                                      expected_manifest_count=1)
    assert result["invalid_metadata"] == 1 and result["deleted_generations"] == 0
    assert env.store.count(env.store.collection_for("conversation")) == 2


@pytest.mark.parametrize("cursor", [
    UUID("df71e69a-5e80-4f1b-8708-584cf878f5ae"),
    grpc.PointId(uuid="df71e69a-5e80-4f1b-8708-584cf878f5ae"),
    grpc.PointId(num=7),
])
def test_sdk_cursor_variants_remain_serializable_and_reusable(environment, monkeypatch, cursor):
    env = environment
    legacy_point(env)
    monkeypatch.setattr(env.store._client, "scroll", lambda **_kwargs: ([], cursor))
    report = review_legacy_generations(env.db, store=env.store, scope="conversation")
    encoded = json.loads(json.dumps(report))
    assert encoded["next_offset"] in {"df71e69a-5e80-4f1b-8708-584cf878f5ae", 7}
