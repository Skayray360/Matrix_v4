# Creado por Aldo Garcia.
"""Vigencia documental y checkpoints SQL con corpus/embeddings sinteticos."""

import json
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.common.errors import DatabaseUnavailableError
from app.common.ids import new_id, utcnow_naive
from app.common.logging import JsonFormatter
from app.database.models import Document, IngestionJob
from app.ingestion.reconciler import ReconcileStats, _stable_digest, reconcile_with_lock
from app.ingestion.service import SourceChangedError
from app.jobs.retention import MaintenanceStats
from app.jobs.scheduler import ReconcileScheduler
from tests.unit import test_knowledge_sync_new as fixtures

pytestmark = pytest.mark.unit
env = fixtures.env
write = fixtures.write
retrieve = fixtures.retrieve


def checkpoint(db, job_type, *, age_hours=0, stats=None, trigger="scheduler", status="completed"):
    finished = utcnow_naive() - timedelta(hours=age_hours)
    job = IngestionJob(id=new_id(), job_type=job_type, status=status, trigger_source=trigger,
                       started_at=finished, finished_at=finished,
                       stats=stats if stats is not None else ReconcileStats().as_dict())
    db.add(job)
    db.commit()
    return job


def test_periodic_sync_retracts_removed_policy_and_updates_changed_hash(env, monkeypatch):
    changed = write(env, "prestaciones/change.md", "# Convenio\n\nVacaciones: veinte dias anteriores.")
    removed = write(env, "prestaciones/removed.md", "# Convenio\n\nPrestacion retirada: treinta dias.")
    reconcile_with_lock(env.db, new_only=True)
    initial = changed.stat()
    removed.unlink()
    changed.write_text("# Convenio\n\nVacaciones: quince dias vigentes.", encoding="utf-8")
    # Una fecha preservada por el editor no debe ocultar el cambio de contenido.
    os.utime(changed, ns=(initial.st_atime_ns, initial.st_mtime_ns))
    monkeypatch.setattr("app.security.admission.snapshot", lambda: {})
    scheduler = ReconcileScheduler()
    scheduler.run_sync_once()
    env.db.expire_all()
    assert scheduler.state.sync_stats["updated_documents"] == 1
    assert scheduler.state.sync_stats["deleted_documents"] == 1
    texts = " ".join(item.text for item in retrieve(env))
    assert "quince dias vigentes" in texts
    assert "veinte dias anteriores" not in texts and "Prestacion retirada" not in texts
    assert env.db.scalar(select(Document).where(Document.filename == removed.name)).deleted_at is not None


def test_incremental_batch_uses_full_inventory_for_deletions_and_rotates_failures(env, monkeypatch):
    first = write(env, "prestaciones/a.md")
    second = write(env, "prestaciones/b.md")
    unchanged = write(env, "prestaciones/c.md")
    reconcile_with_lock(env.db, new_only=True)
    first.write_text("# Convenio\n\nSIEMPRE FALLA vacaciones corregidas.", encoding="utf-8")
    second.write_text("# Convenio\n\nVacaciones vigentes cuarenta dias.", encoding="utf-8")
    original_embed = env.llm.embed

    def embed(texts):
        if any("SIEMPRE FALLA" in text for text in texts):
            raise RuntimeError("Fallo sintetico")
        return original_embed(texts)

    monkeypatch.setattr(env.llm, "embed", embed)
    failed = reconcile_with_lock(env.db, incremental=True, max_documents=1)
    assert failed.pending_documents == 2 and len(failed.failures) == 1
    next_pass = reconcile_with_lock(env.db, incremental=True, max_documents=1)
    assert next_pass.updated_documents == 1 and next_pass.pending_documents == 1
    assert failed.deleted_documents == next_pass.deleted_documents == 0
    assert env.db.scalar(select(Document).where(Document.filename == unchanged.name)).deleted_at is None
    # Un fallo de embeddings conserva la generacion confirmada, como antes.
    assert "veinte dias" in " ".join(item.text for item in retrieve(env))
    assert "cuarenta dias" in " ".join(item.text for item in retrieve(env))


def test_incremental_unchanged_file_does_not_call_embeddings_or_create_jobs(env):
    write(env)
    reconcile_with_lock(env.db, new_only=True)
    calls = len(env.llm.calls)
    jobs = env.db.scalar(select(func.count()).select_from(IngestionJob))
    stats = reconcile_with_lock(env.db, incremental=True, max_documents=1)
    assert stats.unchanged_documents == 1 and stats.updated_documents == 0
    assert len(env.llm.calls) == calls
    assert env.db.scalar(select(func.count()).select_from(IngestionJob)) == jobs


def test_incremental_incomplete_source_never_implies_deletion(env, monkeypatch):
    from app.ingestion import reconciler

    path = write(env)
    reconcile_with_lock(env.db, new_only=True)
    inventory = reconciler.scan_knowledge(knowledge_root=env.settings.knowledge_root_path)
    item = next(item for item in inventory.files if item.absolute_path == path)
    inventory.files = []
    inventory.incomplete_sources.add(item.source_id)
    monkeypatch.setattr(reconciler, "scan_knowledge", lambda **_kwargs: inventory)
    stats = reconcile_with_lock(env.db, incremental=True)
    assert stats.deleted_documents == 0 and stats.preserved_documents == 1
    assert env.db.scalar(select(Document.deleted_at)) is None
    assert retrieve(env)


def test_incremental_unstable_source_is_preserved_until_next_pass(env, monkeypatch):
    path = write(env)
    reconcile_with_lock(env.db, new_only=True)
    path.write_text("# Convenio\n\nActualizacion de vacaciones en progreso.", encoding="utf-8")
    monkeypatch.setattr(env.settings, "rag_sync_stability_seconds", 60)
    stats = reconcile_with_lock(env.db, incremental=True)
    assert stats.deferred_documents == stats.pending_documents == 1
    assert stats.updated_documents == stats.deleted_documents == 0
    assert "veinte dias" in " ".join(item.text for item in retrieve(env))


@pytest.mark.parametrize("mode", [{"force": True}, {"new_only": True}])
def test_incremental_modes_cannot_be_combined(env, mode):
    with pytest.raises(ValueError, match="incompatible"):
        reconcile_with_lock(env.db, incremental=True, **mode)


def test_first_start_runs_full_and_maintenance_then_restarts_honor_sql_checkpoint(env):
    first = ReconcileScheduler()
    first.run_due_once()
    assert first.state.last_status == first.state.maintenance_status == "completed"
    assert first.state.run_count == 1
    for _ in range(3):
        restarted = ReconcileScheduler()
        restarted.run_due_once()
        assert restarted.state.run_count == 0
        assert restarted.state.maintenance_status == "never_run"
    assert env.db.scalar(select(func.count()).select_from(IngestionJob)
                         .where(IngestionJob.job_type == "reconcile")) == 1


def test_daily_shutdown_does_not_reset_expired_checkpoint(env):
    checkpoint(env.db, "reconcile", age_hours=25)
    checkpoint(env.db, "maintenance", age_hours=25)
    restarted = ReconcileScheduler()
    restarted.run_due_once()
    assert restarted.state.run_count == 1
    assert restarted.state.maintenance_status == "completed"


@pytest.mark.parametrize("invalid", [
    {"stats": ReconcileStats(deferred_documents=1).as_dict()},
    {"stats": ReconcileStats(preserved_documents=1).as_dict()},
    {"stats": ReconcileStats(mode="incremental").as_dict()},
    {"trigger": "manual"}, {"status": "failed"}, {"age_hours": -1},
])
def test_incomplete_manual_failed_or_future_checkpoint_never_postpones_full(env, invalid):
    checkpoint(env.db, "reconcile", **invalid)
    checkpoint(env.db, "maintenance")
    scheduler = ReconcileScheduler()
    scheduler.run_due_once()
    assert scheduler.state.run_count == 1 and scheduler.state.last_status == "completed"


def test_failed_maintenance_retries_without_repeating_completed_full(env, monkeypatch):
    maintenance = Mock(side_effect=[RuntimeError("Sintetico"), MaintenanceStats()])
    monkeypatch.setattr("app.jobs.retention.run_maintenance", maintenance)
    scheduler = ReconcileScheduler()
    scheduler.run_due_once()
    assert scheduler.state.last_status == "completed"
    assert scheduler.state.maintenance_status == "error:RuntimeError"
    restarted = ReconcileScheduler()
    restarted.run_due_once()
    assert restarted.state.run_count == 0 and restarted.state.maintenance_status == "completed"
    assert maintenance.call_count == 2


def test_failed_full_retries_without_postponing_successful_maintenance(env, monkeypatch):
    from app.ingestion import reconciler

    original = reconciler.reconcile_with_lock
    full = Mock(side_effect=RuntimeError("Sintetico"))
    monkeypatch.setattr(reconciler, "reconcile_with_lock", full)
    first = ReconcileScheduler()
    first.run_due_once()
    assert first.state.last_status == "error:RuntimeError" and first.state.maintenance_status == "completed"
    monkeypatch.setattr(reconciler, "reconcile_with_lock", original)
    restarted = ReconcileScheduler()
    restarted.run_due_once()
    assert restarted.state.run_count == 1 and restarted.state.maintenance_status == "never_run"


def test_scheduler_logs_sanitized_database_cause_without_sql_or_credentials(env, monkeypatch, caplog):
    driver = Exception(2003, "PRIVATE_PASSWORD PRIVATE_SQL")
    cause = OperationalError("PRIVATE_QUERY", {"password": "PRIVATE_PASSWORD"}, driver)
    error = DatabaseUnavailableError()
    error.__cause__ = cause
    monkeypatch.setattr("app.database.engine.session_scope", Mock(side_effect=error))
    scheduler = ReconcileScheduler()
    scheduler.run_sync_once()
    record = next(record for record in caplog.records if record.message == "scheduler.sync_failed")
    payload = json.loads(JsonFormatter().format(record))
    assert payload["error_type"] == "DatabaseUnavailableError"
    assert payload["cause_type"] == "OperationalError" and payload["database_error_number"] == 2003
    assert "PRIVATE_" not in json.dumps(payload)


def test_failed_start_checks_retry_only_after_interruptible_interval(env, monkeypatch):
    scheduler = ReconcileScheduler()
    events = []
    stopped = False

    def wait(seconds):
        nonlocal stopped
        events.append(("wait", seconds))
        stopped = True

    scheduler._stop = SimpleNamespace(is_set=lambda: stopped, wait=wait)
    monkeypatch.setattr("app.database.engine.session_scope", Mock(side_effect=DatabaseUnavailableError()))
    scheduler._loop(immediate=False)
    assert scheduler.state.last_status == scheduler.state.sync_last_status == "error:DatabaseUnavailableError"
    assert events == [("wait", scheduler.sync_interval)]


def test_full_busy_deferral_is_bounded_and_does_not_postpone_maintenance(env, monkeypatch):
    scheduler = ReconcileScheduler()
    monkeypatch.setattr("app.security.admission.snapshot", lambda: {"chat": 1})
    for _ in range(5):
        scheduler.run_due_once()
        # Las pasadas sync usan un contador distinto, no pueden reiniciar este.
        scheduler._busy_deferrals = 0
        assert scheduler.state.last_status == "deferred_busy"
    assert scheduler.state.run_count == 0 and scheduler.state.maintenance_status == "completed"
    scheduler.run_due_once()
    assert scheduler.state.run_count == 1 and scheduler.state.last_status == "completed"


def test_incremental_hash_rejects_symlink_swap_before_open(tmp_path, monkeypatch):
    path = tmp_path / "source.md"
    path.write_text("synthetic")
    other = tmp_path / "other.md"
    other.write_text("must not be read")
    original_open = os.open

    def replace(source, flags):
        path.unlink()
        path.symlink_to(other)
        return original_open(source, flags)

    monkeypatch.setattr(os, "open", replace)
    with pytest.raises((OSError, SourceChangedError)):
        _stable_digest(path, max_bytes=1024)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires a POSIX filesystem")
def test_incremental_hash_rejects_fifo_swap_without_waiting_for_writer(tmp_path, monkeypatch):
    path = tmp_path / "source.md"
    path.write_text("synthetic")
    original_open = os.open

    def replace(source, flags):
        assert flags & os.O_NONBLOCK
        path.unlink()
        os.mkfifo(path)
        return original_open(source, flags)

    monkeypatch.setattr(os, "open", replace)
    with pytest.raises(SourceChangedError, match="sustituido"):
        _stable_digest(path, max_bytes=1024)


def test_incremental_hash_rejects_size_over_budget(tmp_path):
    path = tmp_path / "oversized.md"
    path.write_bytes(b"abcde")
    with pytest.raises(ValueError, match="limite"):
        _stable_digest(path, max_bytes=4)
