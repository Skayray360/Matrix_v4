# Creado por Aldo Garcia.
"""Procedencia tecnica acotada; no acredita vigencia o aprobacion empresarial."""

from __future__ import annotations

import re
from typing import Any


def source_provenance(values: Any) -> dict[str, str | int]:
    """Lista permitida para evidencia e historial; desconocido permanece ausente."""
    if not isinstance(values, dict):
        return {}
    result: dict[str, str | int] = {}
    for name in ("document_sha256", "index_fingerprint"):
        value = values.get(name)
        if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value):
            result[name] = value.lower()
    for name in ("generation", "embedding_model", "ingestion_version"):
        value = values.get(name)
        if (isinstance(value, str) and value.strip() and len(value) <= 256
                and not any(ord(char) < 32 or ord(char) == 127 for char in value)):
            result[name] = value
    dimension = values.get("embedding_dimension")
    if isinstance(dimension, int) and not isinstance(dimension, bool) and 0 < dimension <= 1_000_000:
        result["embedding_dimension"] = dimension
    return result
