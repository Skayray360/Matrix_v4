# Creado por Aldo Garcia.
"""Exclusion transaccional entre publicacion y limpieza de generaciones RAG."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models import Document


def lock_document(db: Session, document_id: str, *, wait: bool = True) -> Document | None:
    """Bloquea el documento hasta el commit/rollback del caller y relee su estado.

    El flush conserva los cambios pendientes antes de refrescar la identity map;
    un documento nuevo debe quedar insertado antes de escribir su intent durable.
    No se filtra por estado: una generacion activa sigue protegida aunque cambie
    el estado del documento. Un error de lock se propaga; nunca equivale a ausencia.

    ``wait=False`` evita esperar un lock de fila ocupado. Los cleaners deben usar
    una sesion sin cambios pendientes, cuyo flush no necesite locks adicionales.
    SQLite ignora FOR UPDATE: las pruebas adquieren su lock de escritura con un
    UPDATE neutro, sin activar el onupdate del timestamp. En MySQL la lectura
    FOR UPDATE consulta el estado actual incluso bajo REPEATABLE READ.
    """
    db.flush()
    statement = (
        select(Document)
        .where(Document.id == document_id)
        .execution_options(populate_existing=True)
    )
    if db.get_bind().dialect.name != "sqlite":
        return db.execute(statement.with_for_update(nowait=not wait)).scalar_one_or_none()

    connection = db.connection()
    previous_timeout: int | None = None
    if not wait:
        previous_timeout = int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one())
        connection.exec_driver_sql("PRAGMA busy_timeout=0")
    try:
        db.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(id=Document.id, updated_at=Document.updated_at)
            .execution_options(synchronize_session=False)
        )
        return db.execute(statement).scalar_one_or_none()
    finally:
        if previous_timeout is not None:
            connection.exec_driver_sql(f"PRAGMA busy_timeout={previous_timeout}")
