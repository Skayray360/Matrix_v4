# Creado por Aldo Garcia.
"""Vigencia/checkpoints con MySQL y Qdrant descartables; inferencia sintetica."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select, update

from app.common.ids import new_id, utcnow_naive
from app.database.models import Document, IngestionJob
from app.ingestion.reconciler import reconcile_with_lock
from app.jobs.scheduler import ReconcileScheduler
from app.rag.retriever import Retriever
from tests.conftest import make_context
from tests.integration import test_generation_recovery_mysql as fixtures

pytestmark = pytest.mark.integration
mysql_engine = fixtures.mysql_engine
environment = fixtures.environment


@pytest.fixture()
def freshness(environment, tmp_path, monkeypatch):
    env = environment
    root = tmp_path / "knowledge"
    folder = root / "prestaciones"
    folder.mkdir(parents=True)
    suffix = new_id()
    paths = {name: folder / f"{suffix}-{name}.md" for name in ("changed", "removed")}
    relatives = {f"prestaciones/{path.name}" for path in paths.values()}
    settings = env.settings.model_copy(update={"rag_knowledge_root": str(root),
                                                "rag_sync_stability_seconds": 0, "rag_min_similarity": 0})
    for module in ("app.jobs.scheduler", "app.jobs.retention", "app.ingestion.reconciler",
                   "app.ingestion.service", "app.ingestion.knowledge_layout", "app.rag.index_manifest",
                   "app.rag.retriever", "app.rag.vector_store"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    monkeypatch.setattr("app.ingestion.knowledge_layout.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("app.rag.vector_store.get_vector_store", lambda: env.store)
    monkeypatch.setattr("app.ingestion.service.get_vector_store", lambda: env.store)
    llm = fixtures.SyntheticEmbeddings()
    monkeypatch.setattr("app.ingestion.service.get_ollama_client", lambda: llm)
    monkeypatch.setattr("app.security.admission.snapshot", lambda: {})
    own_job_ids = set()

    def tracked_id():
        value = new_id()
        own_job_ids.add(value)
        return value

    monkeypatch.setattr("app.jobs.scheduler.new_id", tracked_id)
    monkeypatch.setattr("app.ingestion.reconciler.new_id", tracked_id)

    # Las funciones de ingesta realizan commits cortos: no mantener un contexto
    # begin() externo que invalide operaciones posteriores en la misma sesion.
    @contextmanager
    def scope_with_commits():
        with env.factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    monkeypatch.setattr("app.database.engine.session_scope", scope_with_commits)
    monkeypatch.setattr("app.rag.index_manifest.session_scope", scope_with_commits)
    try:
        yield SimpleNamespace(env=env, paths=paths, scope=scope_with_commits, llm=llm, own_job_ids=own_job_ids)
    finally:
        # Solo documentos/rutas y jobs de este caso; no limpiar tablas globales.
        with env.factory.begin() as db:
            env.document_ids.update(db.scalars(select(Document.id).where(Document.relative_path.in_(relatives))))
            if own_job_ids:
                db.execute(delete(IngestionJob).where(IngestionJob.id.in_(own_job_ids)))


def test_mysql_periodic_sync_retracts_deleted_and_replaces_changed_source(freshness):
    fx = freshness
    fx.paths["changed"].write_text("# Convenio\n\nVacaciones anteriores veinte dias.", encoding="utf-8")
    fx.paths["removed"].write_text("# Convenio\n\nPrestacion retirada treinta dias.", encoding="utf-8")
    with fx.scope() as db:
        assert reconcile_with_lock(db, new_only=True).new_documents == 2
    fx.paths["changed"].write_text("# Convenio\n\nVacaciones vigentes quince dias.", encoding="utf-8")
    fx.paths["removed"].unlink()
    scheduler = ReconcileScheduler()
    scheduler.run_sync_once()
    assert scheduler.state.sync_last_status == "completed"
    assert scheduler.state.sync_stats["updated_documents"] == scheduler.state.sync_stats["deleted_documents"] == 1
    ctx = make_context(categories=frozenset({"prestaciones"}), wildcard=False)
    retrieved = Retriever(store=fx.env.store, llm=fx.llm).retrieve(
        question="vacaciones convenio", ctx=ctx, authorized_categories=ctx.allowed_categories,
    ).evidences
    texts = " ".join(item.text for item in retrieved)
    assert "quince dias" in texts and "veinte dias" not in texts and "treinta dias" not in texts
    with fx.scope() as observer:
        removed = observer.scalar(select(Document).where(Document.filename == fx.paths["removed"].name))
        assert removed.deleted_at is not None and removed.active_generation is None


def test_mysql_daily_checkpoints_survive_restart_and_expire_in_sql(freshness):
    fx = freshness
    first = ReconcileScheduler()
    first.run_due_once()
    assert first.state.run_count == 1 and first.state.maintenance_status == "completed"
    restarted = ReconcileScheduler()
    restarted.run_due_once()
    assert restarted.state.run_count == 0 and restarted.state.maintenance_status == "never_run"
    with fx.scope() as writer:
        writer.execute(update(IngestionJob).where(IngestionJob.id.in_(fx.own_job_ids))
                       .values(finished_at=utcnow_naive() - timedelta(hours=25)))
    expired = ReconcileScheduler()
    expired.run_due_once()
    assert expired.state.run_count == 1 and expired.state.maintenance_status == "completed"


def test_mysql_full_success_does_not_hide_failed_maintenance_checkpoint(freshness, monkeypatch):
    from app.jobs import retention

    original = retention.run_maintenance
    monkeypatch.setattr(retention, "run_maintenance", Mock(side_effect=RuntimeError("Synthetic failure")))
    first = ReconcileScheduler()
    first.run_due_once()
    assert first.state.run_count == 1 and first.state.maintenance_status == "error:RuntimeError"
    monkeypatch.setattr(retention, "run_maintenance", original)
    restarted = ReconcileScheduler()
    restarted.run_due_once()
    assert restarted.state.run_count == 0 and restarted.state.maintenance_status == "completed"
