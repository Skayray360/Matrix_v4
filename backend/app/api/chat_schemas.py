# Creado por Aldo Garcia.
"""Contratos de salida del chat; un estado incompleto nunca confirma una respuesta.

No valida veracidad ni permisos: esas decisiones corresponden al servicio. Esta
frontera evita publicar estados incoherentes o tipos que el navegador no puede
interpretar. Los campos opcionales conservan operaciones de versiones anteriores.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from app.common.answers import AnswerBasis
from app.common.errors import MatrixError

NonEmptyText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Count = Annotated[int, Field(ge=0, le=9007199254740991)]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class ChatContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class ChatSourceProvenance(ChatContract):
    document_sha256: NonEmptyText | None = None
    generation: NonEmptyText | None = None
    index_fingerprint: NonEmptyText | None = None
    embedding_model: NonEmptyText | None = None
    embedding_dimension: int | None = Field(default=None, ge=1, le=9007199254740991)
    ingestion_version: NonEmptyText | None = None


class ChatSourceResponse(ChatContract):
    source_id: NonEmptyText
    category: str
    filename: str
    section: str
    page_or_sheet: str
    score: float = Field(allow_inf_nan=False)
    label: str
    scope: str
    provenance: ChatSourceProvenance | None = None


class ChatReplyResponse(ChatContract):
    conversation_id: NonEmptyText
    message_id: NonEmptyText
    answer: NonEmptyText
    sources: list[ChatSourceResponse]
    intent: NonEmptyText
    grounded: bool
    answer_basis: AnswerBasis | None = None
    latency_ms: Count


class ChatLatencySummary(ChatContract):
    samples: Count
    p50: Duration | None
    p95: Duration | None
    p99: Duration | None

    @model_validator(mode="after")
    def check_samples(self) -> Self:
        if self.samples == 0:
            if any(value is not None for value in (self.p50, self.p95, self.p99)):
                raise ValueError("No se publican percentiles sin muestras.")
        elif self.p50 is None or self.p95 is None or self.p99 is None or not self.p50 <= self.p95 <= self.p99:
            raise ValueError("Percentiles incompletos o desordenados.")
        return self


class ChatTerminalCounts(ChatContract):
    completed: Count
    failed: Count
    cancelled: Count
    expired: Count


class ChatMetricsResponse(ChatContract):
    sample_limit: int = Field(ge=1, le=9007199254740991)
    sample_size: Count
    cache_ttl_ms: Count
    terminal_counts: ChatTerminalCounts
    queue_ms: ChatLatencySummary
    execution_ms: ChatLatencySummary
    total_ms: ChatLatencySummary

    @model_validator(mode="after")
    def check_population(self) -> Self:
        if self.sample_size > self.sample_limit:
            raise ValueError("La muestra excede el limite declarado.")
        if sum(self.terminal_counts.model_dump().values()) != self.sample_size:
            raise ValueError("Los conteos no corresponden a la muestra.")
        if any(summary.samples > self.sample_size for summary in (self.queue_ms, self.execution_ms, self.total_ms)):
            raise ValueError("Las duraciones exceden la poblacion de la muestra.")
        return self


class ChatCapacityResponse(ChatContract):
    active: Count
    limit: int = Field(ge=1, le=9007199254740991)
    queued: Count
    queue_limit: Count
    utilization_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    overloaded: bool
    inference_active: Count | None = None
    inference_limit: int | None = Field(default=None, ge=1, le=9007199254740991)
    metrics: ChatMetricsResponse | None = None


class ChatTimingResponse(ChatContract):
    queue_ms: Duration | None
    execution_ms: Duration | None
    total_ms: Duration | None


class ChatOperationResponse(ChatContract):
    status: Literal["queued", "running", "completed", "failed", "cancelled", "expired"]
    conversation_id: NonEmptyText
    operation_id: NonEmptyText | None = None
    response: ChatReplyResponse | None
    error_code: NonEmptyText | None = None
    capacity: ChatCapacityResponse
    notice: str | None
    poll_after_ms: int = Field(ge=1000, le=10000)
    timing: ChatTimingResponse | None = None

    @model_validator(mode="after")
    def check_state(self) -> Self:
        if self.status == "completed":
            if self.response is None or self.response.conversation_id != self.conversation_id:
                raise ValueError("Una operacion completada requiere una respuesta de la misma conversacion.")
        elif self.response is not None:
            raise ValueError("Solo una operacion completada puede publicar una respuesta.")
        if self.error_code is not None and self.status != "failed":
            raise ValueError("Solo una operacion fallida puede publicar un codigo de error.")
        return self


class ChatCancellationRequest(ChatContract):
    model_config = ConfigDict(strict=True, extra="forbid")

    conversation_id: NonEmptyText | None = Field(default=None, max_length=36)


class ChatCancellationResponse(ChatContract):
    ok: Literal[True]

    @field_validator("ok", mode="before")
    @classmethod
    def require_true_boolean(cls, value: object) -> bool:
        # Literal[True] tambien reconoce 1 en Python; el contrato JSON no.
        if value is not True:
            raise ValueError("La confirmacion debe ser el booleano true.")
        return True


Contract = TypeVar("Contract", bound=ChatContract)


def validated_chat_response(model: type[Contract], payload: object) -> Contract:
    """No incluir el payload ni el ValidationError en errores/logs publicos."""
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise MatrixError(
            "No fue posible confirmar el resultado. Consulte el estado de la misma solicitud."
        ) from None
