# Creado por Aldo Garcia.
"""Compensaciones vectoriales durables sin conservar nombres ni contenido."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.ids import utcnow_naive
from app.common.logging import get_logger
from app.database.document_locks import lock_document
from app.database.models import IngestionJob
from app.rag.generation_journal import SESSION_WRITE_TRANSACTION
from app.rag.schemas import SCOPE_CONVERSATION, SCOPE_CORPORATE

if TYPE_CHECKING:
    from app.rag.vector_store import VectorStore

logger = get_logger(__name__)
CLEANUP_JOB_TYPE = "generation_cleanup"


def _payload(values: dict[str, str]) -> dict[str, str]:
    result = {key: values.get(key, "") for key in ("document_id", "generation", "scope")}
    if (any(not isinstance(value, str) or not value or len(value) > 128 for value in result.values())
            or result["scope"] not in {SCOPE_CORPORATE, SCOPE_CONVERSATION}):
        raise ValueError("Identificadores de compensacion invalidos.")
    return result


def persist_pending_generation_cleanup(db: Session, values: dict[str, str]) -> bool:
    """Llamar DESPUES del rollback de ingesta; no reutiliza su transaccion."""
    try:
        payload = _payload(values)
        job_id = str(uuid5(NAMESPACE_URL, "matrix-rh:cleanup:" + ":".join(payload.values())))
        if db.get(IngestionJob, job_id) is None:
            db.add(IngestionJob(
                id=job_id, job_type=CLEANUP_JOB_TYPE, status="pending",
                trigger_source="ingestion_compensation", started_at=utcnow_naive(), stats=payload,
            ))
        db.commit()
        logger.warning("ingestion.generation_cleanup_pending")
        return True
    except Exception as exc:  # noqa: BLE001 - preservar el error original de ingesta
        db.rollback()
        logger.error("ingestion.generation_cleanup_persistence_failed", extra={"error_type": type(exc).__name__})
        return False


def retry_pending_generation_cleanup(db: Session, *, limit: int = 8) -> dict[str, int]:
    """Reintentos acotados; no borra nunca una generacion actualmente publicada."""
    from app.rag.vector_store import get_vector_store

    if not 1 <= limit <= 8:
        raise ValueError("El lote de compensacion debe estar entre 1 y 8.")
    _require_resolved_writer(db)
    job_ids = list(db.scalars(select(IngestionJob.id).where(
        IngestionJob.job_type == CLEANUP_JOB_TYPE, IngestionJob.status == "pending",
    ).order_by(func.coalesce(IngestionJob.finished_at, IngestionJob.started_at), IngestionJob.id).limit(limit)))
    counts = {"completed": 0, "failed": 0, "preserved_active": 0}
    for job_id in job_ids:
        try:
            job = db.get(IngestionJob, job_id)
            if job is None or job.status != "pending":
                continue
            payload = _payload(job.stats or {})
            document = lock_document(db, payload["document_id"], wait=False)
            if document is not None and document.scope != payload["scope"]:
                raise ValueError("El scope de la compensacion no coincide con el manifest.")
            active = document.active_generation if document is not None else None
            if active == payload["generation"]:
                counts["preserved_active"] += 1
            else:
                get_vector_store().delete_generation(
                    payload["document_id"], generation=payload["generation"], scope=payload["scope"],
                )
            job.status = "completed"
            job.finished_at = utcnow_naive()
            job.error_message = None
            db.commit()
            counts["completed"] += 1
        except Exception as exc:  # noqa: BLE001 - otro candidato puede limpiarse
            db.rollback()
            counts["failed"] += 1
            try:
                job = db.get(IngestionJob, job_id)
                if job is not None:
                    # Al final de la cola: un fallo repetido no bloquea otros jobs.
                    job.finished_at = utcnow_naive()
                    job.error_message = type(exc).__name__
                    db.commit()
            except Exception as persistence_error:  # noqa: BLE001
                db.rollback()
                logger.error("ingestion.generation_cleanup_persistence_failed", extra={
                    "error_type": type(persistence_error).__name__,
                })
            logger.warning("ingestion.generation_cleanup_retry_failed", extra={"error_type": type(exc).__name__})
    return counts


def _require_resolved_writer(db: Session) -> None:
    writer = db.info.get(SESSION_WRITE_TRANSACTION)
    if writer is not None and db.get_transaction() is writer:
        raise ValueError("La recuperacion requiere resolver primero la transaccion de ingesta.")


def retry_generation_write_intents(db: Session, *, store: VectorStore | None = None, limit: int = 8) -> dict[str, int]:
    """Resuelve commit, rollback o crash sin depender de callbacks del proceso.

    Una transaccion viva conserva el lock aun si la indexacion ya devolvio al
    caller. Si el commit fue ambiguo, la lectura actual bajo lock decide si la
    generacion realmente quedo activa. Nunca se deduce rollback de un timeout.
    """
    from app.rag.vector_store import get_vector_store

    if not 1 <= limit <= 8:
        raise ValueError("El lote de recuperacion debe estar entre 1 y 8.")
    _require_resolved_writer(db)
    vector_store = store or get_vector_store()
    counts = {"completed": 0, "failed": 0, "preserved_active": 0}
    for intent in vector_store.generation_journal.pending(limit=limit):
        try:
            if intent.collection != vector_store.collection_for(intent.scope):
                raise ValueError("La coleccion de la intencion no coincide con la configuracion actual.")
            document = lock_document(db, intent.document_id, wait=False)
            if document is not None and document.scope != intent.scope:
                raise ValueError("El scope de la intencion no coincide con el manifest.")
            active = document is not None and document.active_generation == intent.generation
            if not active:
                vector_store.delete_generation(intent.document_id, generation=intent.generation, scope=intent.scope)
            vector_store.generation_journal.discard(intent)
            db.commit()
            counts["completed"] += 1
            counts["preserved_active"] += int(active)
        except Exception as exc:  # noqa: BLE001 - no borrar ante SQL/lock/estado ambiguo
            db.rollback()
            counts["failed"] += 1
            logger.warning("ingestion.generation_intent_retry_failed", extra={"error_type": type(exc).__name__})
    return counts
