# Creado por Aldo Garcia.
"""Exportacion explicita: ACL antes de scroll, publicacion vigente y destino privado."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, delete, event
from sqlalchemy.orm import Session

from app.common.ids import utcnow_naive
from app.config import QdrantMode
from app.database.models import (
    Base,
    CategoryPermission,
    Conversation,
    Document,
    Permission,
    Role,
    RolePermission,
    StructuredSourcePermission,
    User,
    UserRole,
)
from scripts import diagnosticar_rag as diagnostic
from scripts import rag_chunk_export as exporter

pytestmark = pytest.mark.unit


@pytest.fixture
def source(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'source.sqlite'}")
    tables = [User, Role, Permission, RolePermission, UserRole, CategoryPermission,
              StructuredSourcePermission, Conversation, Document]
    Base.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    with Session(engine) as db:
        db.add_all([
            User(id="owner", username="Operator", display_name="Synthetic", auth_source="local_test"),
            User(id="other", username="Other", display_name="Other", auth_source="local_test"),
            Role(id="reader", name="prestaciones_reader_test"),
            Permission(id="diagnostics", name="diagnostics.read"),
            Permission(id="admin", name="knowledge.admin"),
            UserRole(user_id="owner", role_id="reader"),
            RolePermission(role_id="reader", permission_id="diagnostics"),
            RolePermission(role_id="reader", permission_id="admin"),
            CategoryPermission(role_id="reader", category="prestaciones"),
            Conversation(id="own-chat", user_id="owner", title="Synthetic own conversation"),
            Conversation(id="other-chat", user_id="other", title="Synthetic other conversation"),
        ])
        for name, scope, category, owner, chat in (
            ("visible", "corporate", "prestaciones", None, None),
            ("restricted", "corporate", "nomina", None, None),
            ("private", "conversation", None, "owner", "own-chat"),
            ("other-private", "conversation", None, "other", "other-chat"),
            ("forged-owner", "conversation", None, "owner", "other-chat"),
        ):
            db.add(Document(
                id=name, scope=scope, category=category, owner_user_id=owner, conversation_id=chat,
                filename=f"{name}.txt", mime_type="text/plain", sha256="HASH", chunk_count=3,
                status="indexed", active_generation="published", index_fingerprint="fingerprint",
            ))
        db.commit()
    settings = SimpleNamespace(rag_collection_corporate="corporate", rag_collection_private="private")
    client = QdrantClient(":memory:")
    for collection in ("corporate", "private"):
        client.create_collection(collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    with Session(engine) as db:
        for doc in db.query(Document).all():
            for generation in ("published", "obsolete"):
                points = [models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0], payload={
                    "document_id": doc.id, "scope": doc.scope, "category": doc.category,
                    "owner_user_id": doc.owner_user_id, "conversation_id": doc.conversation_id,
                    "generation": generation, "index_fingerprint": "fingerprint", "chunk_index": index,
                    "text": f"{doc.id} {generation} row {index}", "section": "Table",
                    "page_or_sheet": "Sheet 1", "extraction_incomplete": False,
                    "storage_path": "/SECRET_STORAGE", "embedding_secret": "SECRET_VECTOR_METADATA",
                }) for index in range(3)]
                client.upsert(settings.rag_collection_corporate if doc.scope == "corporate" else "private", points)
    yield engine, client, settings
    client.close()
    engine.dispose()


class ReadOnlyClient:
    def __init__(self, client, *, on_close=None):
        self.client = client
        self.on_close = on_close
        self.calls = []

    def scroll(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["with_vectors"] is False
        return self.client.scroll(**kwargs)

    def close(self):
        if self.on_close:
            self.on_close()


def export(source, tmp_path, *, document_id="visible", conversation_id=None, client=None, **kwargs):
    engine, qdrant, settings = source
    reader = client or ReadOnlyClient(qdrant)
    with engine.connect() as connection:
        event.listen(connection, "before_cursor_execute", diagnostic.select_only_guard)
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
            report = exporter.export_document(
                db, settings=settings, root=tmp_path, destination=tmp_path / "var/review.json",
                username="Operator", document_id=document_id, conversation_id=conversation_id,
                client_factory=lambda _, **_kwargs: reader, **kwargs,
            )
    return report, reader


def test_export_only_authorized_published_document_and_no_writes(source, tmp_path):
    engine, client, _ = source
    with engine.connect() as connection:
        before = list(connection.connection.driver_connection.iterdump())
    points_before = client.count("corporate").count
    report, reader = export(source, tmp_path)
    payload = json.loads((tmp_path / "var/review.json").read_text())
    assert report == {"exported_chunks": 3, "complete": True, "truncated": False}
    assert [row["chunk_index"] for row in payload["chunks"]] == [0, 1, 2]
    assert all(row["text"].startswith("visible published") for row in payload["chunks"])
    assert payload["chunks"][0]["page_or_sheet"] == "Sheet 1"
    assert not any(word in json.dumps(payload) for word in ("restricted", "obsolete", "SECRET"))
    assert "text" not in json.dumps(report) and "visible" not in json.dumps(report)
    assert reader.calls and client.count("corporate").count == points_before
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "var/review.json").stat().st_mode) == 0o600
    with engine.connect() as connection:
        assert before == list(connection.connection.driver_connection.iterdump())


@pytest.mark.parametrize(("document_id", "conversation_id"), [
    ("restricted", None), ("missing", None), ("private", None), ("other-private", "other-chat"),
    ("forged-owner", "other-chat"), ("private", "other-chat"), ("visible", "own-chat"),
])
def test_denied_documents_never_open_qdrant_or_create_file(source, tmp_path, document_id, conversation_id):
    class ForbiddenClient:
        def scroll(self, **kwargs):
            pytest.fail("ACL denial must precede vector retrieval")

        def close(self):
            pytest.fail("ACL denial must precede client creation")

    with pytest.raises(exporter.ExportDenied):
        export(source, tmp_path, document_id=document_id, conversation_id=conversation_id, client=ForbiddenClient())
    assert not (tmp_path / "var/review.json").exists()


@pytest.mark.parametrize("revocation", ["inactive", "permission", "category", "conversation_owner", "ownerless"])
def test_revoked_access_denies_before_scroll(source, tmp_path, revocation):
    engine, client, _ = source
    with Session(engine) as db:
        if revocation == "inactive":
            db.get(User, "owner").is_active = False
        elif revocation == "permission":
            db.execute(delete(RolePermission).where(RolePermission.permission_id == "diagnostics"))
        elif revocation == "category":
            db.execute(delete(CategoryPermission))
        elif revocation == "conversation_owner":
            db.get(Conversation, "own-chat").user_id = "other"
        else:
            db.get(Document, "private").owner_user_id = None
        db.commit()
    reader = ReadOnlyClient(client)
    arguments = {"document_id": "private", "conversation_id": "own-chat"} if revocation in {
        "conversation_owner", "ownerless",
    } else {}
    with pytest.raises(exporter.ExportDenied):
        export(source, tmp_path, client=reader, **arguments)
    assert reader.calls == []


def test_private_owner_requires_live_conversation(source, tmp_path):
    report, _ = export(source, tmp_path, document_id="private", conversation_id="own-chat")
    payload = json.loads((tmp_path / "var/review.json").read_text())
    assert report["complete"] and payload["document"]["owner_user_id"] == "owner"
    assert all(row["text"].startswith("private published") for row in payload["chunks"])


@pytest.mark.parametrize("state", ["deleted_conversation", "purged_conversation", "deleted_document",
                                  "unpublished", "fingerprint_missing", "forged_scope"])
def test_nonlive_private_manifest_denies_read(source, tmp_path, state):
    engine, client, _ = source
    with Session(engine) as db:
        if state == "deleted_conversation":
            db.get(Conversation, "own-chat").deleted_at = utcnow_naive()
        elif state == "purged_conversation":
            db.get(Conversation, "own-chat").purged_at = utcnow_naive()
        elif state == "deleted_document":
            db.get(Document, "private").deleted_at = utcnow_naive()
        elif state == "unpublished":
            db.get(Document, "private").active_generation = None
        elif state == "fingerprint_missing":
            db.get(Document, "private").index_fingerprint = None
        else:
            db.get(Document, "private").scope = "forged"
        db.commit()
    reader = ReadOnlyClient(client)
    with pytest.raises(exporter.ExportDenied):
        export(source, tmp_path, document_id="private", conversation_id="own-chat", client=reader)
    assert reader.calls == []


@pytest.mark.parametrize("change", ["deactivate", "generation", "owner"])
def test_rechecks_authorization_and_publication_before_content_written(source, tmp_path, change):
    engine, client, _ = source

    def revoke():
        with Session(engine) as db:
            if change == "deactivate":
                db.get(User, "owner").is_active = False
            elif change == "generation":
                db.get(Document, "private").active_generation = "new-publication"
            else:
                db.get(Conversation, "own-chat").user_id = "other"
            db.commit()

    with pytest.raises((exporter.ExportDenied, exporter.ExportStateChanged)):
        export(source, tmp_path, document_id="private", conversation_id="own-chat",
               client=ReadOnlyClient(client, on_close=revoke))
    assert not (tmp_path / "var/review.json").exists()


def test_explicit_bounded_partial_export_is_not_reported_complete(source, tmp_path):
    report, reader = export(source, tmp_path, max_chunks=2)
    assert report == {"exported_chunks": 2, "complete": False, "truncated": True}
    assert reader.calls[0]["limit"] == 2


@pytest.mark.parametrize("malformation", ["scope", "owner", "generation", "duplicate", "oversized"])
def test_forged_or_oversized_vector_response_fails_closed(source, tmp_path, monkeypatch, malformation):
    _, client, _ = source
    reader = ReadOnlyClient(client)
    scroll = reader.scroll

    def malformed(**kwargs):
        points, following = scroll(**kwargs)
        if malformation == "duplicate":
            points[1].payload["chunk_index"] = points[0].payload["chunk_index"]
        elif malformation == "oversized":
            monkeypatch.setattr(exporter, "MAX_TEXT_BYTES", 1)
        else:
            points[0].payload[{"scope": "scope", "owner": "owner_user_id", "generation": "generation"}[malformation]] = "forged"
        return points, following

    reader.scroll = malformed
    with pytest.raises((exporter.ExportPayloadMismatch, exporter.ExportLimitExceeded)):
        export(source, tmp_path, document_id="private", conversation_id="own-chat", client=reader)
    assert not (tmp_path / "var/review.json").exists()


def test_missing_chunks_flag_incomplete(source, tmp_path):
    engine, client, settings = source
    with Session(engine) as db:
        document = exporter.authorized_document(db, username="Operator", document_id="visible", conversation_id=None)
    payload = exporter.read_chunks(client, collection=settings.rag_collection_corporate,
                                   document=replace(document, chunk_count=4), max_chunks=128)
    assert not payload["complete"] and not payload["truncated"] and payload["exported_chunks"] == 3


def test_existing_destination_never_truncated_or_reads_vectors(source, tmp_path):
    destination = tmp_path / "var/review.json"
    destination.parent.mkdir()
    destination.write_text("KEEP ORIGINAL")
    reader = ReadOnlyClient(source[1])
    with pytest.raises(FileExistsError):
        export(source, tmp_path, client=reader)
    assert destination.read_text() == "KEEP ORIGINAL" and reader.calls == []


@pytest.mark.parametrize("kind", ["file", "directory", "traversal", "outside", "alternate_stream",
                                 "reserved", "trailing_space"])
def test_output_rejects_symlinks_and_escape(tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "var").mkdir()
    destination = tmp_path / "var/review.json"
    if kind == "file":
        destination.symlink_to(outside / "secret.json")
    elif kind == "directory":
        (tmp_path / "var/link").symlink_to(outside, target_is_directory=True)
        destination = tmp_path / "var/link/review.json"
    elif kind == "traversal":
        destination = tmp_path / "var/../outside/review.json"
    elif kind == "alternate_stream":
        destination = tmp_path / "var/base:stream.json"
    elif kind == "reserved":
        destination = tmp_path / "var/CON.json"
    elif kind == "trailing_space":
        destination = tmp_path / "var/folder /review.json"
    else:
        destination = outside / "review.json"
    with pytest.raises((ValueError, FileExistsError)), exporter.private_output(tmp_path, destination):
        pytest.fail("unsafe output accepted")
    assert list(outside.iterdir()) == []


def test_collect_export_sanitizes_driver_errors_and_never_calls_models(source, tmp_path, monkeypatch):
    engine, _, _ = source
    (tmp_path / "backend/app/config").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("app.database.engine.get_engine", lambda: engine)

    def broken(_, **kwargs):
        raise RuntimeError("SECRET_DOCUMENT mysql://password")

    def forbid_models():
        pytest.fail("chunk export must not call models or inventory")

    monkeypatch.setattr(exporter, "open_existing_store", broken)
    monkeypatch.setattr("app.llm.ollama_client.get_ollama_client", forbid_models)
    report = diagnostic.collect_export(tmp_path, destination=tmp_path / "var/review.json",
                                       username="Operator", document_id="visible")
    assert report["errors"] == [{"component": "chunk_export", "error_type": "RuntimeError"}]
    assert "SECRET" not in json.dumps(report) and not report["inference_executed"]
    assert not (tmp_path / "var/review.json").exists()


@pytest.mark.parametrize("module_variable", ["PSMODULEPATH", "PSModulePath", "psmodulepath"])
def test_windows_acl_command_contains_no_document_text_or_interpolated_path(tmp_path, monkeypatch, module_variable):
    calls = []
    monkeypatch.setenv(module_variable, "SYNTHETIC_POWERSHELL7_MODULES")
    monkeypatch.setattr(exporter.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    path = tmp_path / "name'$() with spaces.json"
    exporter._windows_private_file(path)
    arguments, keywords = calls[0]
    command = arguments[0]
    assert str(path) not in command[-1]
    assert keywords["env"]["MATRIX_REVIEW_FILE"] == str(path)
    assert not any(key.upper() == "PSMODULEPATH" for key in keywords["env"])
    assert os.environ[module_variable] == "SYNTHETIC_POWERSHELL7_MODULES"
    assert "SetAccessRuleProtection($true,$false)" in command[-1]
    assert keywords["check"] and keywords["timeout"] == 15


@pytest.fixture
def embedded(tmp_path):
    path = tmp_path / "original-qdrant"
    client = QdrantClient(path=str(path))
    for collection in ("selected", "other"):
        client.create_collection(collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
        client.upsert(collection, [models.PointStruct(
            id=str(uuid4()), vector=[1.0, 0.0], payload={"text": f"PRIVATE {collection}"},
        )])
    client.close()
    # El SDK 1.20 elimina este duplicado al abrir directamente el indice.
    with closing(sqlite3.connect(path / "collection/selected/storage.sqlite")) as database:
        point = database.execute("SELECT point FROM points LIMIT 1").fetchone()[0]
        database.execute("INSERT INTO points(id,point) VALUES (?,?)", ("legacy-key", point))
        database.commit()
    return SimpleNamespace(qdrant_mode=QdrantMode.EMBEDDED, qdrant_storage_path=path)


def disk_hashes(path):
    return {str(item.relative_to(path)): sha256(item.read_bytes()).hexdigest()
            for item in path.rglob("*") if item.is_file()}


def test_embedded_reads_private_snapshot_without_mutating_source_or_other_collections(embedded):
    before = disk_hashes(embedded.qdrant_storage_path)
    client = exporter.open_existing_store(embedded, collection="selected")
    temporary = Path(client.temporary.name)
    try:
        metadata = json.loads((temporary / "meta.json").read_text())
        assert list(metadata["collections"]) == ["selected"] and metadata["aliases"] == {}
        assert not (temporary / "collection/other").exists()
        if os.name != "nt":
            assert stat.S_IMODE(temporary.stat().st_mode) == 0o700
            assert stat.S_IMODE((temporary / "collection/selected/storage.sqlite").stat().st_mode) == 0o600
        points, _ = client.scroll(collection_name="selected", with_vectors=False)
        assert len(points) == 1 and points[0].payload["text"] == "PRIVATE selected"
    finally:
        client.close()
    assert not temporary.exists()
    assert before == disk_hashes(embedded.qdrant_storage_path)
    with closing(sqlite3.connect(embedded.qdrant_storage_path / "collection/selected/storage.sqlite")) as database:
        assert database.execute("SELECT COUNT(*) FROM points").fetchone()[0] == 2


@pytest.mark.parametrize("budget", ["bytes", "seconds"])
def test_embedded_snapshot_rejects_limit_and_cleans_temporary(embedded, tmp_path, monkeypatch, budget):
    temporary = tmp_path / "snapshots"
    temporary.mkdir()
    monkeypatch.setattr(exporter.tempfile, "tempdir", str(temporary))
    if budget == "bytes":
        monkeypatch.setattr(exporter, "MAX_SNAPSHOT_BYTES", 1)
    else:
        moments = iter([0, exporter.SNAPSHOT_SECONDS + 1])
        monkeypatch.setattr(exporter.time, "monotonic", lambda: next(moments))
    before = disk_hashes(embedded.qdrant_storage_path)
    with pytest.raises(exporter.ExportLimitExceeded):
        exporter.open_existing_store(embedded, collection="selected")
    assert not list(temporary.iterdir())
    assert before == disk_hashes(embedded.qdrant_storage_path)


def test_embedded_requires_existing_lock_and_respects_live_owner(embedded):
    import portalocker

    lock_path = embedded.qdrant_storage_path / ".lock"
    before = disk_hashes(embedded.qdrant_storage_path)
    with lock_path.open("r+b") as lock:
        portalocker.lock(lock, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING)
        try:
            with pytest.raises(portalocker.exceptions.LockException):
                exporter.open_existing_store(embedded, collection="selected")
        finally:
            portalocker.unlock(lock)
    assert before == disk_hashes(embedded.qdrant_storage_path)
    lock_path.unlink()
    with pytest.raises(ValueError, match="existente"):
        exporter.open_existing_store(embedded, collection="selected")
    assert not lock_path.exists()


@pytest.mark.parametrize("sidecar", ["-wal", "-journal"])
def test_embedded_rejects_pending_sqlite_journals(embedded, sidecar):
    source = embedded.qdrant_storage_path / "collection/selected/storage.sqlite"
    Path(str(source) + sidecar).write_bytes(b"synthetic pending transaction")
    before = disk_hashes(embedded.qdrant_storage_path)
    with pytest.raises(ValueError, match="cierre limpio"):
        exporter.open_existing_store(embedded, collection="selected")
    assert before == disk_hashes(embedded.qdrant_storage_path)
