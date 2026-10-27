# Creado por Aldo Garcia.
"""Contrato numerico de embeddings para almacenamiento y coseno float32."""

from __future__ import annotations

import math
import struct


def _float32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def valid_cosine_vector(vector: list[float]) -> bool:
    """Valida representabilidad sin normalizar ni modificar el embedding.

    Qdrant almacena vectores float32. Un vector no nulo en Python puede quedar
    nulo al convertirlo; tambien sus componentes o su norma al cuadrado pueden
    desbordarse. Se verifica la aritmetica float32 antes de cachear/escribir,
    conservando el vector original cuando cumple el contrato.
    """
    if not vector:
        return False
    squares: list[float] = []
    try:
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, int | float):
                return False
            component = _float32(value)
            if not math.isfinite(component):
                return False
            squares.append(_float32(component * component))
        squared_norm = _float32(math.fsum(squares))
    except (OverflowError, ValueError, struct.error):
        # Incluye enteros que no caben siquiera en un float Python.
        return False
    return math.isfinite(squared_norm) and squared_norm > 0.0
