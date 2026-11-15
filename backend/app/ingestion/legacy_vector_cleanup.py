# Creado por Aldo Garcia.
"""Revision explicita de generaciones anteriores al journal durable.

No se invoca desde el scheduler. Un inventario previo y el conteo SQL esperado
son obligatorios antes de aplicar; un manifest vacio requiere habilitacion
adicional del operador para evitar interpretar una BD equivocada como bajas.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.document_locks import lock_document
from app.database.models import Document
from app.rag.generation_journal import SESSION_WRITE_TRANSACTION
from app.rag.schemas import SCOPE_CONVERSATION, SCOPE_CORPORATE
from app.rag.vector_store import VectorStore


def review_legacy_generations(
    db: Session, *, store: VectorStore, scope: str, limit: int = 128,
    offset: str | int | None = None, apply: bool = False,
    expected_manifest_count: int | None = None, allow_empty_manifest: bool = False,
) -> dict[str, Any]:
    """Revisa un lote acotado; protege la generacion activa con current read.

    Los bloqueos y errores SQL detienen el lote antes de borrar el candidato
    afectado. Los borrados anteriores, ya confirmados por Qdrant, son idempotentes
    al reintentar el mismo offset. El modo preview no modifica vectores ni SQL.
    """
    if scope not in {SCOPE_CORPORATE, SCOPE_CONVERSATION} or not 1 <= limit <= 128:
        raise ValueError("Scope o lote de mantenimiento invalido.")
    writer = db.info.get(SESSION_WRITE_TRANSACTION)
    if writer is not None and db.get_transaction() is writer:
        raise ValueError("Resuelva la transaccion de ingesta antes del mantenimiento.")
    if db.new or db.dirty or db.deleted:
        raise ValueError("El mantenimiento requiere una sesion sin cambios pendientes.")
    manifest_count = db.scalar(select(func.count()).select_from(Document))
    if apply and (expected_manifest_count is None or expected_manifest_count != manifest_count):
        raise ValueError("El conteo SQL no coincide con el inventario confirmado por el operador.")
    if apply and not manifest_count and not allow_empty_manifest:
        raise ValueError("Un manifest SQL vacio requiere --allow-empty-manifest despues de verificar el entorno.")
    rows, following = store.scan_generation_metadata(scope=scope, limit=limit, offset=offset)
    report: dict[str, Any] = {
        "apply": apply, "scope": scope, "manifest_count": manifest_count, "scanned_points": len(rows),
        "preserved_active": 0, "invalid_metadata": 0, "candidate_generations": 0,
        "deleted_generations": 0, "next_offset": following, "candidates": [],
    }
    # Terminar la lectura de inventario: cada decision posterior usa una nueva
    # transaccion y un bloqueo puntual, no el snapshot inicial del conteo.
    db.rollback()
    seen: set[tuple[str, str]] = set()
    for payload in rows:
        document_id, generation = payload.get("document_id"), payload.get("generation")
        if (payload.get("scope") != scope
                or not isinstance(document_id, str) or not document_id or len(document_id) > 128
                or not isinstance(generation, str) or not generation or len(generation) > 128):
            report["invalid_metadata"] += 1
            continue
        identity = (document_id, generation)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            document = lock_document(db, document_id, wait=False)
            if document is not None and document.scope != scope:
                report["invalid_metadata"] += 1
            elif document is not None and document.active_generation == generation:
                report["preserved_active"] += 1
            else:
                report["candidate_generations"] += 1
                report["candidates"].append({"document_id": document_id, "generation": generation})
                if apply:
                    store.delete_generation(document_id, generation=generation, scope=scope)
                    report["deleted_generations"] += 1
        finally:
            # El lock no autoriza cambios SQL. Ni siquiera el UPDATE neutro de
            # SQLite necesita un commit en esta herramienta de mantenimiento.
            db.rollback()
    return report
