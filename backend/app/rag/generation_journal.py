# Creado por Aldo Garcia.
"""Intenciones durables previas a escribir una generacion, sin texto documental.

Viven en Qdrant para no confirmar ni abrir una segunda escritura SQL mientras
el llamador conserva su transaccion. El bloqueo SQL del documento, mantenido
hasta commit/rollback, decide cuando otro proceso puede resolver la intencion.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from app.common.errors import QdrantUnavailableError
from app.common.logging import get_logger
from app.rag.schemas import SCOPE_CONVERSATION, SCOPE_CORPORATE

logger = get_logger(__name__)
SESSION_WRITE_TRANSACTION = "vector_generation_write_transaction"


@dataclass(frozen=True, slots=True)
class GenerationIntent:
    document_id: str
    generation: str
    scope: str
    collection: str

    def __post_init__(self) -> None:
        if (any(not isinstance(value, str) or not value or len(value) > 255 for value in asdict(self).values())
                or self.scope not in {SCOPE_CORPORATE, SCOPE_CONVERSATION}):
            raise ValueError("Identificadores de intencion vectorial invalidos.")

    @property
    def id(self) -> str:
        identity = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return str(uuid5(NAMESPACE_URL, "matrix-rh:generation-intent:" + identity))


class GenerationJournal:
    """Coleccion sin vectores; nunca participa en ranking ni contiene contenido."""

    def __init__(self, *, client: QdrantClient, collection: str) -> None:
        self._client = client
        self.collection = collection
        self._lock = threading.RLock()
        self._cursor: Any | None = None

    def _ensure(self) -> None:
        with self._lock:
            if not self._client.collection_exists(self.collection):
                try:
                    self._client.create_collection(collection_name=self.collection, vectors_config={})
                except Exception:
                    # Otro proceso pudo crearla entre exists y create.
                    if not self._client.collection_exists(self.collection):
                        raise
            if self._client.get_collection(self.collection).config.params.vectors != {}:
                raise ValueError("La coleccion de intenciones debe estar separada y no contener vectores.")

    def record(self, intent: GenerationIntent) -> None:
        """Debe terminar antes del primer upsert; un resultado ambiguo lo impide."""
        try:
            self._ensure()
            result = self._client.upsert(
                collection_name=self.collection, wait=True, ordering=models.WriteOrdering.STRONG,
                points=[models.PointStruct(id=intent.id, vector={}, payload={"schema_version": 1, **asdict(intent)})],
            )
            if result.status != models.UpdateStatus.COMPLETED:
                raise ValueError("La intencion vectorial no quedo confirmada.")
        except Exception as exc:  # noqa: BLE001 - nunca seguir escribiendo sin intencion durable
            raise QdrantUnavailableError("No se pudo registrar la intencion de escritura vectorial.") from exc

    def pending(self, *, limit: int = 8) -> list[GenerationIntent]:
        """Lectura acotada y rotatoria; una intencion fallida no bloquea la cola."""
        if not 1 <= limit <= 128:
            raise ValueError("El lote de intenciones debe estar entre 1 y 128.")
        try:
            with self._lock:
                if not self._client.collection_exists(self.collection):
                    self._cursor = None
                    return []
                points, self._cursor = self._client.scroll(
                    collection_name=self.collection, offset=self._cursor, limit=limit,
                    with_payload=models.PayloadSelectorInclude(include=[
                        "schema_version", "document_id", "generation", "scope", "collection",
                    ]), with_vectors=False,
                )
            intents = []
            for point in points:
                try:
                    payload = point.payload or {}
                    if payload.get("schema_version") != 1:
                        raise ValueError("Version de intencion desconocida.")
                    intent = GenerationIntent(**{key: payload[key] for key in (
                        "document_id", "generation", "scope", "collection",
                    )})
                    if intent.id != str(point.id):
                        raise ValueError("La identidad de la intencion no coincide.")
                    intents.append(intent)
                except (KeyError, TypeError, ValueError):
                    # Conservar el registro inconsistente para revision, sin
                    # inferir que sea seguro borrar ningun documento.
                    logger.error("ingestion.invalid_generation_intent")
            return intents
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailableError("No se pudieron leer las intenciones vectoriales.") from exc

    def discard(self, intent: GenerationIntent) -> None:
        """Solo tras verificar SQL bajo lock y completar/preservar la generacion."""
        try:
            if not self._client.collection_exists(self.collection):
                return
            result = self._client.delete(
                collection_name=self.collection, points_selector=[intent.id],
                wait=True, ordering=models.WriteOrdering.STRONG,
            )
            if result.status != models.UpdateStatus.COMPLETED:
                raise ValueError("No se confirmo el cierre de la intencion.")
        except Exception as exc:  # noqa: BLE001 - reintentar es seguro
            raise QdrantUnavailableError("No se pudo cerrar la intencion vectorial.") from exc
