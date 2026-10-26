# Creado por Aldo Garcia.
"""Chunking estructural.

Prioridad de separadores (seccion 9): encabezados/secciones -> parrafos ->
listas -> tablas/filas -> frases completas.

Regla explicita del proyecto: **no partir una lista corta o un procedimiento
breve** si cabe entero dentro del tamano maximo. Partir un procedimiento de seis
pasos en dos chunks es la causa mas comun de respuestas incompletas en un RAG de
politicas de RH.

La estimacion compartida contempla caracteres, UTF-8 y cadenas sin espacios.
No sustituye al tokenizador del runtime. Una unidad semantica demasiado grande
se rechaza expresamente; no se cortan condiciones ni se omite contenido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.common.text_budget import TextBudget, estimate_tokens

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM_RE = re.compile(r"^\s*([-*+•]|\d+[.)])\s+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_DOCUMENT_HEADING_RE = re.compile(r"^\s*(?:[🔹🔸▪■●•]\s*)?CHUNK\s+\d+\s*[—–:\-]\s*(\S.*)$", re.IGNORECASE)


class ChunkBudgetExceededError(ValueError):
    """Unidad indivisible fuera de presupuesto; no conserva texto documental."""

    code = "chunk_budget_exceeded"

    def __init__(self, unit: str, *, budget: TextBudget) -> None:
        super().__init__(f"{unit} excede el limite de fragmentacion "
                         f"({budget.max_tokens} tokens estimados, {budget.max_chars} caracteres, "
                         f"{budget.max_utf8_bytes} bytes UTF-8). Divida la unidad en la fuente "
                         "conservando sus condiciones y vuelva a indexar.")


#: Un chunk no se corta en un encabezado si aun no acumulo este minimo de
#: tokens. Evita generar chunks que solo contienen un titulo, que no aportan
#: evidencia y ensucian la recuperacion.
HEADING_BREAK_MIN_TOKENS = 80


@dataclass(frozen=True, slots=True)
class TextBlock:
    """Bloque estructural extraido de un documento."""

    text: str
    section: str
    page_or_sheet: str = ""
    kind: str = "paragraph"  # paragraph | heading | list | table | rows | code
    #: Nivel del encabezado (1-6). Solo relevante cuando ``kind == "heading"``.
    level: int = 0
    #: Marcadores explicitos de seccion en PDF: no mezclar temas cortos.
    split_before: bool = False


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """Chunk antes de recibir metadata de documento."""

    text: str
    section: str
    page_or_sheet: str
    index: int


def split_into_blocks(
    text: str, *, default_section: str = "", document_headings: bool = False, markdown_tables: bool = False,
) -> list[TextBlock]:
    """Convierte texto plano/Markdown en bloques estructurales.

    Se agrupan las lineas consecutivas del mismo tipo: una lista completa es un
    bloque, una tabla completa es un bloque. Asi el chunker puede tratarlas como
    unidades indivisibles cuando caben.
    """
    blocks: list[TextBlock] = []
    section = default_section
    buffer: list[str] = []
    buffer_kind = "paragraph"
    fence = ""
    table_columns = 0

    def flush() -> None:
        nonlocal buffer, buffer_kind
        content = "\n".join(buffer).strip("\n") if buffer_kind == "list" else "\n".join(buffer).strip()
        if content.strip():
            blocks.append(TextBlock(text=content, section=section, kind=buffer_kind))
        buffer = []
        buffer_kind = "paragraph"

    lines = text.splitlines()
    for position, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        if fence:
            buffer.append(raw_line)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                flush()
                fence = ""
            continue
        if buffer_kind == "list" and line[:1].isspace():
            # Una condicion, sublista o bloque indentado pertenece al elemento
            # anterior. No convertirlo en un parrafo independiente recuperable.
            buffer.append(raw_line)
            continue
        opening = _FENCE_RE.match(line)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            flush()
            fence = opening[1]
            buffer_kind = "code"
            buffer.append(raw_line)
            table_columns = 0
            continue
        if markdown_tables:
            cells = _markdown_cells(line)
            if table_columns and len(cells) == table_columns:
                line = "| " + " | ".join(cells) + " |"
            else:
                table_columns = 0
                following = _markdown_cells(lines[position + 1]) if position + 1 < len(lines) else []
                if cells and len(cells) == len(following) and all(
                    re.fullmatch(r":?-+:?", cell) for cell in following
                ):
                    table_columns = len(cells)
                    line = "| " + " | ".join(cells) + " |"
        heading = _HEADING_RE.match(line)
        document_heading = _DOCUMENT_HEADING_RE.match(line) if document_headings else None
        if document_heading:
            flush()
            section = document_heading.group(1).strip()
            blocks.append(TextBlock(text=line.strip(), section=section, kind="heading", level=2, split_before=True))
            continue
        if heading:
            flush()
            section = heading.group(2).strip()
            blocks.append(
                TextBlock(
                    text=line.strip(),
                    section=section,
                    kind="heading",
                    level=len(heading.group(1)),
                )
            )
            continue
        if not line.strip():
            if buffer_kind == "list":
                buffer.append("")
                continue
            flush()
            continue

        kind = "paragraph"
        if _TABLE_ROW_RE.match(line):
            kind = "table"
        elif _LIST_ITEM_RE.match(line):
            kind = "list"
        elif buffer_kind == "list" and buffer and buffer[-1].strip():
            # Markdown admite continuaciones de parrafo sin sangria mientras
            # no haya una linea vacia que abra otro bloque.
            kind = "list"

        if buffer and kind != buffer_kind:
            flush()
        buffer_kind = kind
        buffer.append(line)

    flush()
    return blocks


def _markdown_cells(line: str) -> list[str]:
    """Separar barras sin confundir una barra escapada con una columna."""
    cells: list[str] = []
    start = 0
    escaped = False
    for position, char in enumerate(line):
        if char == "|" and not escaped:
            cells.append(line[start:position].strip())
            start = position + 1
        escaped = char == "\\" and not escaped
    if not cells:
        return []
    cells.append(line[start:].strip())
    if not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def chunk_blocks(
    blocks: list[TextBlock], *, chunk_size_tokens: int, overlap_tokens: int
) -> list[ChunkDraft]:
    """Agrupa bloques en chunks respetando limites estructurales.

    Un bloque indivisible (lista o tabla) que cabe en el tamano maximo nunca se
    parte. Un bloque mayor se divide solo por frases, filas o elementos completos.
    Si una unidad no cabe, falla la ingesta en vez de publicar una fuente parcial.
    """
    if chunk_size_tokens <= overlap_tokens:
        raise ValueError("chunk_size_tokens debe ser mayor que overlap_tokens")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens no puede ser negativo")
    budget = TextBudget.from_tokens(chunk_size_tokens)
    overlap_budget = TextBudget.from_tokens(overlap_tokens)

    drafts: list[ChunkDraft] = []
    current: list[TextBlock] = []
    current_tokens = 0
    index = 0
    table_context: list[TextBlock] = []
    context_key: tuple[str, str] | None = None

    def emit(items: list[TextBlock]) -> None:
        nonlocal index
        content = "\n\n".join(b.text for b in items).strip()
        if not content:
            return
        if not budget.fits(content):
            raise ChunkBudgetExceededError("Un bloque indivisible", budget=budget)
        section = next((b.section for b in items if b.section), "")
        page = next((b.page_or_sheet for b in items if b.page_or_sheet), "")
        drafts.append(ChunkDraft(text=content, section=section, page_or_sheet=page, index=index))
        index += 1

    for block in blocks:
        key = (block.page_or_sheet, block.section)
        if key != context_key or block.kind == "heading":
            table_context = []
            context_key = key

        # Una cita tiene una unica pagina/hoja. No atribuir texto de la siguiente
        # pagina a la primera ni arrastrar solape a traves de esa frontera.
        if current and block.page_or_sheet != current[-1].page_or_sheet:
            emit(current)
            current, current_tokens = [], 0

        # Un encabezado es frontera estructural: abre chunk nuevo aunque el
        # actual no este lleno. Sin esta regla un documento corto entero cae en
        # un unico chunk y su embedding queda diluido entre temas distintos, que
        # es justo lo que hunde la recuperacion en un corpus de politicas.
        if (
            block.kind == "heading"
            and current
            and (current_tokens >= HEADING_BREAK_MIN_TOKENS or block.split_before)
        ):
            emit(current)
            current, current_tokens = [], 0

        if block.kind == "table":
            prefix = "\n\n".join(item.text for item in table_context)
            if (
                not budget.fits("\n\n".join([prefix, block.text]))
                or any(item not in current for item in table_context)
            ):
                if current:
                    emit(current)
                    current, current_tokens = [], 0
                for piece in _split_table(block, chunk_size_tokens, context=prefix):
                    emit([piece])
                continue
        else:
            # Las condiciones anteriores de esta seccion/pagina acompanan cada
            # parte de una tabla. Nunca tomar reglas de otra pagina o seccion.
            table_context.append(block)

        if not budget.fits(block.text):
            # Bloque gigante: se cierra lo acumulado y se parte por frases.
            if current:
                emit(current)
                current, current_tokens = [], 0
            for piece in _split_oversized(block, chunk_size_tokens):
                emit([piece])
            continue

        if current and not budget.fits("\n\n".join(b.text for b in [*current, block])):
            emit(current)
            # El solape comparte el presupuesto con el bloque entrante. Antes
            # podia producir 910 tokens con un limite de 900. Se calcula sobre
            # el texto unido para no acumular el redondeo de bloques pequenos.
            carry: list[TextBlock] = []
            for previous in reversed(current):
                proposed = [previous, *carry]
                if not overlap_budget.fits("\n\n".join(b.text for b in proposed)):
                    break
                if not budget.fits("\n\n".join(b.text for b in [*proposed, block])):
                    break
                carry.insert(0, previous)
            current = carry

        current.append(block)
        current_tokens = estimate_tokens("\n\n".join(b.text for b in current))

    if current:
        emit(current)
    return drafts


def _split_oversized(block: TextBlock, chunk_size_tokens: int) -> list[TextBlock]:
    """Divide por unidades completas, nunca por una cantidad de palabras."""
    if block.kind == "table":
        return _split_table(block, chunk_size_tokens)
    if block.kind in {"rows", "code", "list"}:
        return _split_atomic_lines(block, chunk_size_tokens)
    budget = TextBudget.from_tokens(chunk_size_tokens)
    sentences = re.split(r"(?<=[.!?])\s+", block.text)
    pieces: list[TextBlock] = []
    buffer: list[str] = []

    for sentence in sentences:
        if not budget.fits(sentence):
            raise ChunkBudgetExceededError("Una frase indivisible", budget=budget)
        if buffer and not budget.fits(" ".join([*buffer, sentence])):
            pieces.append(TextBlock(" ".join(buffer), block.section, block.page_or_sheet, block.kind))
            buffer = []
        buffer.append(sentence)

    if buffer:
        pieces.append(TextBlock(" ".join(buffer), block.section, block.page_or_sheet, block.kind))
    return pieces


def _split_atomic_lines(block: TextBlock, chunk_size_tokens: int) -> list[TextBlock]:
    """Mantener cada fila con sus columnas y el codigo con sus delimitadores."""
    lines = block.text.splitlines()
    budget = TextBudget.from_tokens(chunk_size_tokens)
    if block.kind == "list":
        indent = min((len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())
                      for line in lines if _LIST_ITEM_RE.match(line)), default=0)
        items: list[str] = []
        for line in lines:
            level = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())
            if not items or (_LIST_ITEM_RE.match(line) and level <= indent):
                items.append(line)
            else:
                items[-1] += "\n" + line
        lines = items
    prefix: list[str] = []
    suffix: list[str] = []
    if block.kind == "code":
        opening = _FENCE_RE.match(lines[0])
        if opening:
            prefix = [lines.pop(0)]
            suffix = [opening[1]]
            if lines and re.fullmatch(
                r" {0,3}" + re.escape(opening[1][0]) + "{" + str(len(opening[1])) + r",}\s*", lines[-1],
            ):
                suffix = [lines.pop()]
    pieces: list[TextBlock] = []
    rows: list[str] = []

    def content(items: list[str]) -> str:
        return "\n".join([*prefix, *items, *suffix])

    for line in lines:
        if not budget.fits(content([line])):
            raise ChunkBudgetExceededError("Una fila, elemento de lista o linea de codigo", budget=budget)
        if rows and not budget.fits(content([*rows, line])):
            pieces.append(TextBlock(content(rows), block.section, block.page_or_sheet, block.kind))
            rows = []
        rows.append(line)
    if rows or not pieces:
        pieces.append(TextBlock(content(rows), block.section, block.page_or_sheet, block.kind))
    return pieces


def _split_table(block: TextBlock, chunk_size_tokens: int, *, context: str = "") -> list[TextBlock]:
    """Conserva filas atomicas y repite columnas/unidades en cada fragmento.

    Cortar por palabras desvincula los valores de la fila y del encabezado.
    Si ni siquiera una fila y sus columnas caben, el documento necesita revision;
    no se indexa una tabla parcial con cifras cuyo significado ya se perdio.
    """
    lines = [line.strip() for line in block.text.splitlines() if line.strip()]
    budget = TextBudget.from_tokens(chunk_size_tokens)
    if not lines:
        return []
    header = [lines[0]]
    start = 1
    if len(lines) > 1 and re.fullmatch(r"\|?[\s:|\-]+\|?", lines[1]):
        header.append(lines[1])
        start = 2
    pieces: list[TextBlock] = []
    rows: list[str] = []

    def content(data_rows: list[str]) -> str:
        table = "\n".join([*header, *data_rows])
        return "\n\n".join(part for part in (context, table) if part)

    def emit() -> None:
        pieces.append(TextBlock(content(rows), block.section, block.page_or_sheet, block.kind))

    if not budget.fits(content([])):
        raise ChunkBudgetExceededError("Las condiciones y el encabezado de la tabla", budget=budget)
    for row in lines[start:]:
        if not budget.fits(content([row])):
            raise ChunkBudgetExceededError("Una fila, sus condiciones y encabezado de tabla", budget=budget)
        if rows and not budget.fits(content([*rows, row])):
            emit()
            rows = []
        rows.append(row)
    if rows or not pieces:
        emit()
    return pieces


def chunk_text(
    text: str, *, chunk_size_tokens: int, overlap_tokens: int, default_section: str = ""
) -> list[ChunkDraft]:
    """Atajo: bloques + chunking en una sola llamada."""
    return chunk_blocks(
        split_into_blocks(text, default_section=default_section),
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=overlap_tokens,
    )
