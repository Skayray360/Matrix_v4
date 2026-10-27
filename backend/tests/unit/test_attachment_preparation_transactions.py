# Creado por Aldo Garcia.
"""Preparacion de lotes privados sin SQL; publicacion revalidada con datos sinteticos."""
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import event, func, select

from app.api.routes import conversations
from app.common.errors import IngestionFailedError, NotFoundError, UnauthorizedError, ValidationFailedError
from app.common.ids import utcnow_naive
from app.database.models import Conversation, Document, SessionRecord, User
from app.rag.schemas import SCOPE_CONVERSATION
from tests.conftest import make_context
from tests.unit import test_private_reindex_final as private_fixtures

pytestmark = pytest.mark.unit
environment = private_fixtures.environment


@pytest.fixture
def upload_state(environment, monkeypatch):
    env = environment
    env.context = make_context(user_id="user-a")
    env.db.add(SessionRecord(
        id=env.context.session_id, session_token_hash="1" * 64, user_id="user-a",
        csrf_token="synthetic", auth_source="local_test",
        expires_at=utcnow_naive() + timedelta(hours=1),
    ))
    env.db.commit()
    monkeypatch.setattr(conversations, "get_settings", lambda: env.settings)
    monkeypatch.setattr(conversations, "get_ingestion_service", lambda: env.service)
    monkeypatch.setattr("app.auth.sessions.get_settings", lambda: env.settings)
    monkeypatch.setattr("app.agents.chat_queue.get_chat_queue", lambda: SimpleNamespace(ensure_owner=lambda db: None))
    monkeypatch.setattr(conversations, "get_audit_service", lambda: SimpleNamespace(record=lambda *args: None))
    return env


def upload(env, count=2):
    files = [UploadFile(filename=f"synthetic-{i}.txt", file=BytesIO(b"The toy has a blue square.")) for i in range(count)]
    return conversations.upload_attachments("chat-a", files=files, db=env.db, ctx=env.context)


def assert_not_published(env):
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(Document)) == 0
    assert env.store.count(env.store.collection_for(SCOPE_CONVERSATION)) == 0
    assert not list(env.settings.upload_storage_path.glob("*/*/*"))


def test_entire_batch_prepares_without_sql_transaction_and_publishes_together(upload_state, monkeypatch):
    env = upload_state
    original = env.service.prepare_attachment
    observed = []

    def prepare(**kwargs):
        assert not env.db.in_transaction()
        with env.factory() as other:
            assert other.scalar(select(func.count()).select_from(Document)) == 0
            # Escritura independiente: la preparacion no conserva el lock del usuario.
            other.get(User, "user-a").display_name = "Synthetic concurrent update"
            other.commit()
        observed.append(kwargs["filename"])
        return original(**kwargs)

    monkeypatch.setattr(env.service, "prepare_attachment", prepare)
    result = upload(env)
    assert len(observed) == len(result.documents) == 2
    with env.factory() as other:
        assert other.scalar(select(func.count()).select_from(Document)) == 0
    env.db.commit()
    with env.factory() as other:
        assert other.scalar(select(func.count()).select_from(Document)) == 2


def test_orphan_quota_is_checked_before_any_inference(upload_state, monkeypatch):
    env = upload_state
    env.settings.upload_max_documents_per_user = 1
    path = env.settings.upload_storage_path / "user-a" / "chat-a" / "orphan.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"orphan")
    monkeypatch.setattr(env.service, "prepare_attachment", lambda **kw: pytest.fail("Inference before quota"))
    with pytest.raises(ValidationFailedError, match="cuota"):
        upload(env, 1)


def test_preparation_failure_does_not_publish_an_earlier_file(upload_state, monkeypatch):
    env = upload_state
    original = env.service.prepare_attachment
    calls = 0

    def prepare(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IngestionFailedError("synthetic failure")
        return original(**kwargs)

    monkeypatch.setattr(env.service, "prepare_attachment", prepare)
    with pytest.raises(IngestionFailedError, match="synthetic failure"):
        upload(env)
    assert_not_published(env)


@pytest.mark.parametrize("change", ["conversation", "session", "user"])
@pytest.mark.parametrize("file_count", [1, 2])
def test_revocation_during_preparation_never_publishes(upload_state, monkeypatch, change, file_count):
    env = upload_state
    original = env.service.prepare_attachment

    def prepare(**kwargs):
        result = original(**kwargs)
        with env.factory() as other:
            if change == "conversation":
                other.get(Conversation, "chat-a").deleted_at = utcnow_naive()
            elif change == "session":
                other.get(SessionRecord, env.context.session_id).revoked_at = utcnow_naive()
            else:
                other.get(User, "user-a").is_active = False
            other.commit()
        return result

    monkeypatch.setattr(env.service, "prepare_attachment", prepare)
    with pytest.raises((NotFoundError, UnauthorizedError, IngestionFailedError)):
        upload(env, file_count)
    assert_not_published(env)


def test_quota_change_during_preparation_is_rechecked(upload_state, monkeypatch):
    env = upload_state
    env.settings.upload_max_documents_per_user = 1
    original = env.service.prepare_attachment

    def prepare(**kwargs):
        result = original(**kwargs)
        with env.factory() as other:
            other.add(Document(id="other", scope=SCOPE_CONVERSATION, owner_user_id="user-a",
                               conversation_id="chat-a", filename="toy.txt", mime_type="text/plain",
                               size_bytes=1, sha256="2" * 64, status="pending"))
            other.commit()
        return result

    monkeypatch.setattr(env.service, "prepare_attachment", prepare)
    with pytest.raises(ValidationFailedError, match="cuota"):
        upload(env, 1)
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(Document)) == 1
    assert not list(env.settings.upload_storage_path.glob("*/*/*"))


def test_preparation_budget_rejects_before_embeddings(upload_state, monkeypatch):
    env = upload_state
    monkeypatch.setattr(env.llm, "embed", lambda *args: pytest.fail("Embedding despite budget"))
    with pytest.raises(ValidationFailedError, match="presupuesto"):
        env.service.prepare_attachment(data=b"synthetic body", filename="toy.txt", max_memory_bytes=1)


def test_prepared_file_cannot_be_published_as_different_input(upload_state):
    env = upload_state
    prepared = env.service.prepare_attachment(data=b"blue", filename="toy.txt")
    with pytest.raises(IngestionFailedError, match="no corresponde"):
        env.service.ingest_conversation_attachment(
            env.db, data=b"red", display_name="toy.txt", internal_filename="toy.txt",
            mime_type="text/plain", owner_user_id="user-a", conversation_id="chat-a", prepared=prepared,
        )
    assert_not_published(env)


def test_publication_failure_keeps_vectors_invisible_and_orphan_bytes_counted(upload_state, monkeypatch):
    env = upload_state
    original = env.service.ingest_conversation_attachment
    calls = 0

    def publish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IngestionFailedError("synthetic publication failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(env.service, "ingest_conversation_attachment", publish)
    with pytest.raises(IngestionFailedError, match="publication failure"):
        upload(env)
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(Document)) == 0
    from tests.unit.test_private_reindex_final import summary

    assert not summary(env).evidences
    assert len(list(env.settings.upload_storage_path.glob("*/*/*"))) == 1
    env.settings.upload_max_documents_per_user = 1
    with pytest.raises(ValidationFailedError, match="cuota"):
        env.service.check_attachment_batch_quota(
            env.db, owner_user_id="user-a", conversation_id="chat-a", incoming_bytes=1, incoming_documents=1,
        )


@pytest.mark.parametrize("when", ["during", "between"])
def test_changed_embedding_fingerprint_is_never_relabelled_or_published(upload_state, monkeypatch, when):
    env = upload_state
    if when == "during":
        original = env.llm.embed

        def embed(texts):
            result = original(texts)
            env.settings.rag_chunk_size_tokens += 1
            return result

        monkeypatch.setattr(env.llm, "embed", embed)
    else:
        original = env.service.prepare_attachment

        def prepare(**kwargs):
            result = original(**kwargs)
            env.settings.rag_chunk_size_tokens += 1
            return result

        monkeypatch.setattr(env.service, "prepare_attachment", prepare)
    with pytest.raises(IngestionFailedError, match="configuracion cambio"):
        upload(env, 1)
    assert_not_published(env)


@pytest.mark.parametrize("budget", [0, -1])
def test_exhausted_batch_budget_rejects_before_extraction(upload_state, monkeypatch, budget):
    monkeypatch.setattr("app.ingestion.service.extract_document", lambda *a, **kw: pytest.fail("Parser despite budget"))
    with pytest.raises(ValidationFailedError, match="presupuesto"):
        upload_state.service.prepare_attachment(data=b"synthetic", filename="toy.txt", max_memory_bytes=budget)


def test_publication_flushes_session_before_locking_conversation(upload_state, monkeypatch):
    env = upload_state
    original = env.service.prepare_attachment
    publishing = False
    events = []

    def prepare(**kwargs):
        nonlocal publishing
        result = original(**kwargs)
        publishing = True
        return result

    def flush(session, context, instances):
        if publishing and any(isinstance(row, SessionRecord) for row in session.dirty):
            events.append("session-flush")

    def execute(state):
        if publishing and state.is_select and state.statement._for_update_arg is not None:
            entities = [item.get("entity") for item in state.statement.column_descriptions]
            if Conversation in entities:
                events.append("conversation-lock")

    monkeypatch.setattr(env.service, "prepare_attachment", prepare)
    event.listen(env.db, "before_flush", flush)
    event.listen(env.db, "do_orm_execute", execute)
    try:
        upload(env, 1)
    finally:
        event.remove(env.db, "before_flush", flush)
        event.remove(env.db, "do_orm_execute", execute)
    assert "session-flush" in events and "conversation-lock" in events
    assert events.index("session-flush") < events.index("conversation-lock")
