# Creado por Aldo Garcia.
"""Presupuesto determinista sin tokenizador, red ni dependencias del modelo.

No es un conteo exacto ni garantiza la tokenizacion de cualquier modelo. ASCII
normal se estima a tres caracteres/token; puntuacion, UTF-8 no ASCII y cadenas
largas sin espacios reciben un cargo mayor. Los limites fisicos se comprueban
por separado para no confundir caracteres Python con bytes UTF-8.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

TEXT_BUDGET_VERSION = "utf8-structural-v1"
CHARS_PER_TOKEN_LIMIT = 3
UTF8_BYTES_PER_TOKEN_LIMIT = 3
_LONG_UNBROKEN_CHARS = 48


def estimate_tokens(text: str) -> int:
    """Aproximacion conservadora: no tratar CJK, codigo o una URL como una palabra."""
    if not text:
        return 0
    utf8_bytes = len(text.encode("utf-8"))
    ascii_count = sum(char.isascii() for char in text)
    punctuation = sum(char.isascii() and not (char.isalnum() or char.isspace()) for char in text)
    words = text.split()
    long_run_charge = sum(
        (2 * sum(char.isascii() and char.isalnum() for char in word) + 2) // 3
        for word in words if len(word) >= _LONG_UNBROKEN_CHARS
    )
    estimate = ((ascii_count - punctuation + 2) // 3 + punctuation
                + utf8_bytes - ascii_count + long_run_charge)
    return max(estimate, (len(words) * 13 + 9) // 10)


@dataclass(frozen=True, slots=True)
class TextBudget:
    """Tres limites explicitos, medidos sobre las mismas unidades completas."""

    max_tokens: int
    max_chars: int
    max_utf8_bytes: int

    @classmethod
    def from_tokens(cls, tokens: int) -> TextBudget:
        tokens = max(0, tokens)
        return cls(tokens, tokens * CHARS_PER_TOKEN_LIMIT, tokens * UTF8_BYTES_PER_TOKEN_LIMIT)

    def fits(self, text: str) -> bool:
        return (len(text) <= self.max_chars
                and len(text.encode("utf-8")) <= self.max_utf8_bytes
                and estimate_tokens(text) <= self.max_tokens)

    def fits_parts(self, parts: Iterable[str]) -> bool:
        chars = utf8_bytes = tokens = 0
        for part in parts:
            chars += len(part)
            utf8_bytes += len(part.encode("utf-8"))
            if chars > self.max_chars or utf8_bytes > self.max_utf8_bytes:
                return False
            tokens += estimate_tokens(part)
            if tokens > self.max_tokens:
                return False
        return True
