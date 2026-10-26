# Creado por Aldo Garcia.
"""Generaciones visibles: Qdrant almacena, SQL activa despues de escritura completa."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import or_, select

from app.common.errors import IndexUpdateRequiredError
from app.common.text_budget import TEXT_BUDGET_VERSION
from app.config import get_settings
from app.database.engine import session_scope
from app.database.models import Document

# v11: presupuesto UTF-8 compartido; unidades indivisibles y guardas de salida.
# La nueva huella evita mezclar extracciones de distintas versiones.
PIPELINE_VERSION = 11


def indexing_fingerprint(llm=None) -> str:
    settings = get_settings()
    revision = settings.llm_embedding_revision
    if llm is not None and hasattr(llm, "embedding_revision"):
        revision = llm.embedding_revision()
    values = {
        "pipeline": PIPELINE_VERSION,
        "token_estimator": TEXT_BUDGET_VERSION,
        "embedding_overflow_policy": (
            "ollama_truncate_false" if settings.llm_embedding_provider == "ollama" else "runtime_defined"
        ),
        # v4 aplica el limite de filas/columnas durante la lectura de XLSX.
        # La huella obliga a reextraer documentos con bytes identicos para que
        # un indice anterior no conserve filas ahora fuera del limite.
        "extractor": 7,
        "ocr_enabled": settings.extraction_ocr_enabled,
        "ocr_language": settings.extraction_ocr_language,
        "ocr_dpi": settings.extraction_ocr_dpi,
        "model": settings.ollama_embedding_model,
        "revision": revision,
        "provider": settings.llm_embedding_provider,
        "endpoint": settings.ollama_base_url
        if settings.llm_embedding_provider == "ollama"
        else settings.llm_api_base_url,
        "dimension": settings.rag_embedding_dimension,
        "size": settings.rag_chunk_size_tokens,
        "overlap": settings.rag_chunk_overlap_tokens,
        "query_template": settings.llm_query_template,
        "document_template": settings.llm_document_template,
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def visible_candidates(candidates):
    """Solo generaciones activadas y documentos no borrados llegan al modelo."""
    ids = {str(item.payload.get("document_id", "")) for item in candidates}
    if not ids:
        return []
    with session_scope() as db:
        rows = db.execute(
            select(Document.id, Document.active_generation, Document.index_fingerprint).where(
                Document.id.in_(ids), Document.deleted_at.is_(None), Document.status == "indexed"
            )
        ).all()
        active = {row.id: (row.active_generation, row.index_fingerprint) for row in rows}
    return [
        item
        for item in candidates
        if active.get(str(item.payload.get("document_id")))
        == (item.payload.get("generation"), item.payload.get("index_fingerprint"))
        and item.payload.get("generation")
    ]


def require_current_index(db, authorized_query, fingerprint: str) -> None:
    """Bloquea un alcance parcialmente obsoleto sin revelar fuentes ajenas.

    El caller debe aplicar primero categoria o propietario+conversacion. Solo
    documentos publicados cuentan: pendientes, fallidos y bajas no bloquean.
    Una mezcla de generaciones actuales y antiguas requiere reindexar antes de
    contestar, para no presentar la parte actual como si cubriera todo el alcance.
    """
    stale = authorized_query.where(
        Document.status == "indexed", Document.deleted_at.is_(None),
        Document.active_generation.is_not(None),
        or_(Document.index_fingerprint.is_(None), Document.index_fingerprint != fingerprint),
    ).with_only_columns(Document.id).limit(1)
    if db.execute(stale).first() is not None:
        raise IndexUpdateRequiredError()


def active_filter(query_filter):
    """Aplica generacion ANTES del ranking: candidatos nuevos no desplazan al activo."""
    from qdrant_client import models

    query = select(Document.active_generation).where(
        Document.status == "indexed", Document.deleted_at.is_(None), Document.active_generation.is_not(None)
    )
    fingerprint = None
    for condition in query_filter.must or []:
        field = getattr(condition, "key", "")
        column = {
            "document_id": Document.id,
            "generation": Document.active_generation,
            "scope": Document.scope,
            "category": Document.category,
            "owner_user_id": Document.owner_user_id,
            "conversation_id": Document.conversation_id,
            "index_fingerprint": Document.index_fingerprint,
        }.get(field)
        if column is None:
            continue
        match = getattr(condition, "match", None)
        if field == "index_fingerprint" and hasattr(match, "value"):
            fingerprint = match.value
            continue
        if hasattr(match, "value"):
            query = query.where(column == match.value)
        elif hasattr(match, "any"):
            query = query.where(column.in_(match.any))
    with session_scope() as db:
        if fingerprint is not None:
            require_current_index(db, query, fingerprint)
            query = query.where(Document.index_fingerprint == fingerprint)
        generations = list(db.execute(query).scalars())
    return models.Filter(
        must=[
            query_filter,
            models.FieldCondition(key="generation", match=models.MatchAny(any=generations or ["__none__"])),
        ]
    )
