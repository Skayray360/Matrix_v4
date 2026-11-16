# Creado por Aldo Garcia.
"""Sincronizacion incremental y mantenimiento diario con vencimiento durable.

Se implementa con un hilo demonio y un ``Event`` en lugar de una libreria de
scheduling: la unica tarea periodica del sistema es esta, y un hilo con
``Event.wait`` es interrumpible al apagar el proceso, no requiere dependencias y
no introduce un segundo modelo de concurrencia en el backend.

La proteccion frente a ejecuciones simultaneas no vive aqui sino en el lock de
base de datos (``reconcile_with_lock``): asi tambien queda cubierto el caso de
dos procesos (por ejemplo, el servidor y una ejecucion manual del script).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import timedelta

from app.common.ids import new_id, utcnow_naive
from app.common.logging import get_logger, safe_error_fields
from app.config import get_settings

logger = get_logger(__name__)


@dataclass
class SchedulerState:
    running: bool = False
    last_run_at: str | None = None
    last_status: str = "never_run"
    run_count: int = 0
    maintenance_status: str = "never_run"
    maintenance_stats: dict[str, int] | None = None
    sync_last_run_at: str | None = None
    sync_last_status: str = "never_run"
    sync_run_count: int = 0
    sync_stats: dict[str, object] | None = None


class ReconcileScheduler:
    """Ejecuta la reconciliacion del knowledge root periodicamente."""

    def __init__(self, *, interval_hours: int | None = None) -> None:
        settings = get_settings()
        self.interval = timedelta(
            hours=interval_hours if interval_hours is not None else settings.rag_reindex_interval_hours
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = SchedulerState()
        self.sync_interval = settings.rag_sync_interval_seconds
        self._busy_deferrals = 0
        self._full_busy_deferrals = 0
        self._run_lock = threading.Lock()

    def start(self, *, run_immediately: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="matrixrh-reconcile", daemon=True, kwargs={"immediate": run_immediately}
        )
        self._thread.start()
        self.state.running = True
        logger.info(
            "scheduler.started", extra={"interval_hours": self.interval.total_seconds() / 3600}
        )

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self.state.running = False
        logger.info("scheduler.stopped")

    def _loop(self, *, immediate: bool) -> None:
        if immediate:
            self.run_once()
        while not self._stop.is_set():
            # El vencimiento vive en SQL: reiniciar a diario no vuelve a posponer
            # la reconciliacion ni la purga durante otras 24 horas.
            if not immediate:
                self.run_due_once()
            immediate = False
            if not self._stop.is_set():
                self.run_sync_once()
            # Un arranque sin BD o una pasada parcial se reintentan, con espera
            # interrumpible y sin bucle inmediato ni demora fija de 24 horas.
            self._stop.wait(max(1, self.sync_interval))

    def run_sync_once(self) -> None:
        """Altas/cambios estables y bajas, con prioridad al chat y lote acotado."""
        from sqlalchemy import select

        from app.database.engine import session_scope
        from app.database.models import ChatOperation
        from app.ingestion.reconciler import reconcile_with_lock
        from app.security.admission import snapshot

        if not self._run_lock.acquire(blocking=False):
            return
        try:
            with session_scope() as db:
                active = snapshot()
                busy = bool(active.get("chat") or active.get("inference") or db.scalar(
                    select(ChatOperation.id).where(ChatOperation.status == "queued").limit(1)
                ))
                if busy and self._busy_deferrals < 5:
                    self._busy_deferrals += 1
                    self.state.sync_last_status = "deferred_busy"
                    return
                # Tras cinco aplazamientos intentar un documento; el cupo de
                # inferencia existente sigue siendo obligatorio, sin otra cola.
                stats = reconcile_with_lock(db, trigger="scheduler_sync", incremental=True,
                                            max_documents=1 if busy else 8)
                self._busy_deferrals = 0
            self.state.sync_run_count += 1
            self.state.sync_stats = stats.as_dict() if stats is not None else None
            self.state.sync_last_status = (
                "skipped_locked" if stats is None else "partial_failure" if stats.failures
                else "pending" if stats.pending_documents or stats.deferred_documents or stats.preserved_documents
                else "completed"
            )
        except Exception as exc:  # noqa: BLE001 - reintentar en la siguiente pasada
            self.state.sync_last_status = f"error:{type(exc).__name__}"
            logger.error("scheduler.sync_failed", extra=safe_error_fields(exc))
        finally:
            self.state.sync_last_run_at = utcnow_naive().isoformat()
            self._run_lock.release()

    def run_once(self) -> None:
        """Una pasada de reconciliacion. Nunca propaga excepciones al hilo."""
        self._run_scheduled(due_only=False)

    def run_due_once(self) -> None:
        """Consulta checkpoints SQL; solo ejecuta las tareas diarias vencidas."""
        self._run_scheduled(due_only=True)

    def _run_scheduled(self, *, due_only: bool) -> None:
        from sqlalchemy import select

        from app.database.engine import session_scope
        from app.database.models import ChatOperation, IngestionJob
        from app.security.admission import snapshot

        if not self._run_lock.acquire(blocking=False):
            return
        try:
            full_due = maintenance_due = True
            if due_only:
                with session_scope() as db:
                    now = utcnow_naive()

                    def due(job_type: str) -> bool:
                        latest = db.scalar(select(IngestionJob).where(
                            IngestionJob.job_type == job_type, IngestionJob.trigger_source == "scheduler",
                            IngestionJob.status == "completed", IngestionJob.finished_at.is_not(None),
                        ).order_by(IngestionJob.finished_at.desc(), IngestionJob.id.desc()).limit(1))
                        if latest is None or latest.finished_at is None:
                            return True
                        if job_type == "reconcile":
                            stats = latest.stats or {}
                            if not isinstance(stats, dict) or stats.get("mode") != "full" or any(
                                stats.get(key, 0) for key in (
                                    "failure_count", "pending_documents", "deferred_documents", "preserved_documents",
                                )
                            ):
                                return True
                        # Un reloj atrasado tampoco debe suspender tareas indefinidamente.
                        return latest.finished_at > now or now - latest.finished_at >= self.interval

                    full_due, maintenance_due = due("reconcile"), due("maintenance")
                    if full_due:
                        active = snapshot()
                        busy = bool(active.get("chat") or active.get("inference") or db.scalar(
                            select(ChatOperation.id).where(ChatOperation.status == "queued").limit(1)
                        ))
                        if busy and self._full_busy_deferrals < 5:
                            self._full_busy_deferrals += 1
                            self.state.last_status = "deferred_busy"
                            full_due = False
                        else:
                            self._full_busy_deferrals = 0
            if maintenance_due:
                self._run_maintenance()
            if full_due and not self._stop.is_set():
                self._run_full()
        except Exception as exc:  # noqa: BLE001 - el siguiente tick vuelve a consultar SQL
            self.state.last_status = f"error:{type(exc).__name__}"
            self.state.last_run_at = utcnow_naive().isoformat()
            logger.error("scheduler.schedule_failed", extra=safe_error_fields(exc))
        finally:
            self._run_lock.release()

    def _run_maintenance(self) -> None:
        from app.database.engine import session_scope
        from app.database.models import IngestionJob
        from app.jobs.retention import run_maintenance

        # Checkpoint separado: una reconciliacion exitosa no es evidencia de
        # mantenimiento exitoso, ni viceversa. No se necesita una tabla nueva.
        started = utcnow_naive()
        try:
            with session_scope() as db:
                maintenance = run_maintenance(db)
                db.add(IngestionJob(
                    id=new_id(), job_type="maintenance", trigger_source="scheduler",
                    status="pending" if maintenance.pending_cleanup else "completed",
                    started_at=started, finished_at=utcnow_naive(), stats=maintenance.to_dict(),
                ))
            self.state.maintenance_stats = maintenance.to_dict()
            self.state.maintenance_status = "pending_cleanup" if maintenance.pending_cleanup else "completed"
        except Exception as exc:  # noqa: BLE001 - una tarea no mata la otra
            self.state.maintenance_status = f"error:{type(exc).__name__}"
            logger.error("scheduler.maintenance_failed", extra=safe_error_fields(exc))

    def _run_full(self) -> None:
        from app.database.engine import session_scope
        from app.ingestion.reconciler import reconcile_with_lock

        try:
            with session_scope() as db:
                stats = reconcile_with_lock(db, trigger="scheduler")
            self.state.last_status = (
                "skipped_locked" if stats is None else "partial_failure" if stats.failures
                else "pending" if stats.pending_documents or stats.deferred_documents or stats.preserved_documents
                else "completed"
            )
            self.state.run_count += 1
        except Exception as exc:  # noqa: BLE001 - un fallo no debe matar el hilo
            self.state.last_status = f"error:{type(exc).__name__}"
            logger.error("scheduler.run_failed", extra=safe_error_fields(exc))
        finally:
            self.state.last_run_at = utcnow_naive().isoformat()


_scheduler: ReconcileScheduler | None = None


def get_scheduler() -> ReconcileScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ReconcileScheduler()
    return _scheduler
