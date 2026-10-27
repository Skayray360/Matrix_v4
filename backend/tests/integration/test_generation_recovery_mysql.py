# Creado por Aldo Garcia.
"""Protocolo de generaciones con MySQL y Qdrant reales, sin modelos.

Esta suite exige servicios locales descartables y migraciones ya aplicadas.
No omite casos si un servicio falta: el job dirigido debe fallar y conservar
ese limite de validacion. Cada caso usa IDs y colecciones propios. Qdrant usa
el modo configurado; MATRIX_TEST_REQUIRE_QDRANT_SERVER=true exige server en CI.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import Engine, create_engine, delete, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.common.ids import new_id
from app.config import QdrantMode, get_settings
from app.config.settings import PROJECT_ROOT, Settings
from app.database.document_locks import lock_document
from app.database.models import Document
from app.ingestion.pending_generation_cleanup import retry_generation_write_intents
from app.ingestion.service import IngestionService
from app.rag.vector_store import VectorStore
from scripts.test_environment import require_disposable_database

pytestmark = pytest.mark.integration
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class SyntheticEmbeddings:
    """Solo sustituye inferencia; SQL, locks, journal e indice son reales."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        dimension = get_settings().rag_embedding_dimension
        return [[1.0] + [0.0] * (dimension - 1) for _ in texts]


def index_candidate(db: Session, store: VectorStore, document_id: str, data: bytes) -> str:
    document = db.get(Document, document_id)
    if document is None:
        document = Document(
            id=document_id, scope="corporate", category="prestaciones",
            filename="synthetic.md", mime_type="text/markdown",
            relative_path=f"prestaciones/{document_id}.md", sha256=hashlib.sha256(data).hexdigest(),
        )
        db.add(document)
    else:
        document.sha256 = hashlib.sha256(data).hexdigest()
    outcome = IngestionService(store=store, llm=SyntheticEmbeddings())._index_document(
        db, document=document, data=data, category="prestaciones",
    )
    assert outcome.chunk_count == 1
    assert document.active_generation is not None
    return document.active_generation


@pytest.fixture(scope="module")
def mysql_engine() -> Iterator[Engine]:
    # La guardia se ejecuta antes de conectar, no solo antes del primer DELETE.
    require_disposable_database()
    settings = get_settings()
    url = make_url(settings.database_url.get_secret_value())
    assert url.drivername == "mysql+pymysql", "Esta suite exige MySQL real mediante PyMySQL."
    assert url.host in LOOPBACK_HOSTS, "La suite solo acepta un MySQL local descartable."
    require_server = os.environ.get("MATRIX_TEST_REQUIRE_QDRANT_SERVER", "").lower()
    assert require_server in {"", "true", "false"}, "MATRIX_TEST_REQUIRE_QDRANT_SERVER debe ser true/false."
    if require_server == "true":
        assert settings.qdrant_mode is QdrantMode.SERVER, "El job dirigido exige Qdrant server."
    if settings.qdrant_mode is QdrantMode.SERVER:
        assert urlsplit(settings.qdrant_url).hostname in LOOPBACK_HOSTS, "Qdrant debe ser local y descartable."
    engine = create_engine(
        url, isolation_level="REPEATABLE READ", hide_parameters=True,
        connect_args={"connect_timeout": 5, "read_timeout": 15, "write_timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def bound_lock_wait(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 2")

    try:
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT VERSION()"))
            assert "MariaDB" not in version, "La evidencia de este job debe corresponder a MySQL."
            assert connection.scalar(text("SELECT @@transaction_isolation")) == "REPEATABLE-READ"
            assert inspect(connection).has_table("documents"), "Ejecute las migraciones antes de la suite."
            assert inspect(connection).has_table("document_versions"), "Faltan migraciones de documentos."
        yield engine
    finally:
        engine.dispose()


@dataclass
class RecoveryEnvironment:
    factory: sessionmaker[Session]
    store: VectorStore
    settings: Settings
    document_ids: set[str] = field(default_factory=set)

    def new_document_id(self) -> str:
        document_id = new_id()
        self.document_ids.add(document_id)
        return document_id

    def recover(self) -> dict[str, int]:
        with self.factory() as cleaner:
            return retry_generation_write_intents(cleaner, store=self.store)

    def generations(self) -> set[str]:
        points, offset = self.store.scan_generation_metadata(scope="corporate", limit=128)
        assert offset is None, "Los fixtures solo deben generar unos pocos chunks."
        return {point["generation"] for point in points}

    def seed(self, document_id: str) -> str:
        with self.factory.begin() as writer:
            generation = index_candidate(writer, self.store, document_id, b"Synthetic previous policy.")
        assert self.recover() == {"completed": 1, "failed": 0, "preserved_active": 1}
        return generation


def vector_client(settings: Settings) -> QdrantClient:
    if settings.qdrant_mode is QdrantMode.EMBEDDED:
        return QdrantClient(path=str(settings.qdrant_storage_path))
    return QdrantClient(
        url=settings.qdrant_url, timeout=10, trust_env=False, check_compatibility=False,
        api_key=settings.qdrant_api_key.get_secret_value() or None,
    )


@pytest.fixture()
def environment(mysql_engine, monkeypatch) -> Iterator[RecoveryEnvironment]:
    suffix = new_id().replace("-", "")
    test_root = PROJECT_ROOT / "var" / "tests"
    assert test_root.resolve() == test_root.absolute(), "El directorio de pruebas no debe redirigir a otro almacen."
    storage = test_root / f"vector-recovery-{suffix}"
    settings = get_settings().model_copy(update={
        "rag_collection_corporate": f"matrix_rh_test_vectors_{suffix}_corporate",
        "rag_collection_private": f"matrix_rh_test_vectors_{suffix}_private",
        "qdrant_path": storage,
    })
    for module in ("app.ingestion.service", "app.rag.vector_store", "app.rag.index_manifest"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    env = None
    client = None
    namespace_available = False
    storage_created = False
    try:
        if settings.qdrant_mode is QdrantMode.EMBEDDED:
            # Un directorio nuevo evita compartir el lock de Qdrant con Matrix
            # o con otro caso. Nunca se abre el path embedded de la instalacion.
            storage.mkdir(parents=True, exist_ok=False)
            storage_created = True
        client = vector_client(settings)
        store = VectorStore(client=client)
        env = RecoveryEnvironment(sessionmaker(mysql_engine, expire_on_commit=False), store, settings)
        collections = (
            settings.rag_collection_corporate, settings.rag_collection_private,
            store.generation_journal.collection,
        )
        # Disponibilidad comprobada de verdad, sin convertir errores en skips.
        client.get_collections()
        for collection in collections:
            assert not client.collection_exists(collection), "Colision inesperada del namespace de pruebas."
        namespace_available = True
        yield env
    finally:
        # Solo los IDs generados por este caso, nunca truncate/drop global.
        try:
            if env is not None and env.document_ids:
                with env.factory.begin() as cleaner:
                    cleaner.execute(delete(Document).where(Document.id.in_(env.document_ids)))
        finally:
            try:
                if env is not None and namespace_available:
                    # El caso de caida embedded puede haber reabierto el cliente.
                    client = env.store._client
                    for collection in collections:
                        if client.collection_exists(collection):
                            client.delete_collection(collection)
            finally:
                try:
                    if env is not None:
                        env.store.close()
                    elif client is not None:
                        client.close()
                finally:
                    if storage_created:
                        assert storage.resolve() == storage.absolute(), "El fixture no debe borrar enlaces."
                        assert storage.parent == test_root
                        shutil.rmtree(storage)


@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
@pytest.mark.parametrize("commit", [False, True], ids=["rollback", "commit"])
def test_live_writer_is_not_treated_as_absent_then_recovers_after_resolution(environment, existing, commit):
    env = environment
    document_id = env.new_document_id()
    previous = env.seed(document_id) if existing else None
    with env.factory() as writer, env.factory() as blocked:
        candidate = index_candidate(writer, env.store, document_id, b"Synthetic candidate policy.")
        with pytest.raises(OperationalError) as failure:
            lock_document(blocked, document_id, wait=False)
        assert failure.value.orig.args[0] == 3572, "MySQL debe rechazar NOWAIT por el lock vivo."
        blocked.rollback()
        assert env.recover() == {"completed": 0, "failed": 1, "preserved_active": 0}
        assert env.generations() == ({previous, candidate} if existing else {candidate})
        assert len(env.store.generation_journal.pending()) == 1
        if commit:
            writer.commit()
        else:
            writer.rollback()

    assert env.recover() == {"completed": 1, "failed": 0, "preserved_active": int(commit)}
    assert env.generations() == ({previous} if existing else set()) | ({candidate} if commit else set())
    assert not env.store.generation_journal.pending()
    with env.factory() as observer:
        # La recuperacion tambien debe liberar su propio lock al terminar.
        document = lock_document(observer, document_id, wait=False)
        if commit or existing:
            assert document.active_generation == (candidate if commit else previous)
        else:
            assert document is None


@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
def test_recovery_uses_current_read_despite_an_older_repeatable_read_snapshot(environment, existing):
    env = environment
    document_id = env.new_document_id()
    previous = env.seed(document_id) if existing else None
    with env.factory() as cleaner:
        stale = cleaner.get(Document, document_id)
        assert (stale.active_generation if stale is not None else None) == previous
        with env.factory.begin() as writer:
            candidate = index_candidate(writer, env.store, document_id, b"Synthetic committed policy.")
        # Un SELECT normal sigue viendo el snapshot anterior en esta transaccion.
        assert cleaner.scalar(select(Document.active_generation).where(Document.id == document_id)) == previous
        assert retry_generation_write_intents(cleaner, store=env.store) == {
            "completed": 1, "failed": 0, "preserved_active": 1,
        }
    assert env.generations() == ({previous, candidate} if existing else {candidate})
    assert not env.store.generation_journal.pending()


def test_locked_document_does_not_prevent_resolving_another_document(environment):
    env = environment
    locked_id, free_id = env.new_document_id(), env.new_document_id()
    old_locked, old_free = env.seed(locked_id), env.seed(free_id)
    with env.factory() as writer:
        pending = index_candidate(writer, env.store, locked_id, b"Synthetic pending policy.")
        with env.factory.begin() as other_writer:
            published = index_candidate(other_writer, env.store, free_id, b"Synthetic published policy.")
        assert env.recover() == {"completed": 1, "failed": 1, "preserved_active": 1}
        assert {intent.document_id for intent in env.store.generation_journal.pending()} == {locked_id}
        assert env.generations() == {old_locked, old_free, pending, published}
        writer.rollback()
    assert env.recover() == {"completed": 1, "failed": 0, "preserved_active": 0}
    assert env.generations() == {old_locked, old_free, published}


def test_lost_commit_response_preserves_the_generation_actually_committed(environment):
    env = environment
    document_id = env.new_document_id()

    def response_lost(_session):
        raise RuntimeError("Synthetic lost commit response")

    with env.factory() as writer:
        candidate = index_candidate(writer, env.store, document_id, b"Synthetic durable policy.")
        event.listen(writer, "after_commit", response_lost)
        try:
            with pytest.raises(RuntimeError, match="Synthetic lost commit response"):
                writer.commit()
        finally:
            event.remove(writer, "after_commit", response_lost)
    assert env.recover() == {"completed": 1, "failed": 0, "preserved_active": 1}
    assert env.generations() == {candidate}
    assert not env.store.generation_journal.pending()


@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
def test_abrupt_process_exit_leaves_a_recoverable_intent(environment, existing):
    env = environment
    document_id = env.new_document_id()
    previous = env.seed(document_id) if existing else None
    child_environment = os.environ.copy()
    child_environment.update({
        "RAG_COLLECTION_CORPORATE": env.settings.rag_collection_corporate,
        "RAG_COLLECTION_PRIVATE": env.settings.rag_collection_private,
        "QDRANT_MODE": env.settings.qdrant_mode.value,
        "QDRANT_PATH": str(env.settings.qdrant_storage_path),
    })
    child_code = """
import os
import sys
from app.database.engine import get_sessionmaker
from app.rag.vector_store import VectorStore
from scripts.test_environment import require_disposable_database
from tests.integration.test_generation_recovery_mysql import index_candidate
require_disposable_database()
db = get_sessionmaker()()
store = VectorStore()
index_candidate(db, store, sys.argv[1], b'Synthetic interrupted policy.')
os._exit(17)
"""
    embedded = env.settings.qdrant_mode is QdrantMode.EMBEDDED
    if embedded:
        env.store.close()
    try:
        completed = subprocess.run(
            [sys.executable, "-c", child_code, document_id], env=child_environment,
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
            timeout=60, check=False,
        )
    finally:
        if embedded:
            env.store = VectorStore(client=vector_client(env.settings))
    # No se vuelcan excepciones remotas/DSN del subproceso al reporte JUnit.
    assert completed.returncode == 17, f"El proceso de fixture termino con codigo {completed.returncode}."
    assert len(env.store.generation_journal.pending()) == 1
    assert len(env.generations()) == 1 + int(existing)
    deadline = time.monotonic() + 5
    while True:
        recovered = env.recover()
        if recovered == {"completed": 1, "failed": 0, "preserved_active": 0}:
            break
        # MySQL puede tardar brevemente en procesar el cierre de socket y el
        # rollback del proceso muerto. Durante ese intervalo, nada se elimina.
        assert recovered == {"completed": 0, "failed": 1, "preserved_active": 0}
        assert len(env.store.generation_journal.pending()) == 1
        assert len(env.generations()) == 1 + int(existing)
        assert time.monotonic() < deadline, "MySQL no libero la transaccion del proceso terminado."
        time.sleep(0.05)
    assert env.generations() == ({previous} if existing else set())
    assert not env.store.generation_journal.pending()
    with env.factory() as observer:
        document = observer.get(Document, document_id)
        assert (document is not None) is existing
        if existing:
            assert document.active_generation == previous
