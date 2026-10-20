# Creado por Aldo Garcia.
"""Inspeccion acotada de imagenes PDF sin decodificarlas ni depender de Pillow."""

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import ContentStream

from app.common.errors import ExtractionFailedError

MAX_GRAPHIC_OBJECTS = 256
MAX_FORM_DEPTH = 16


def has_raster_images(page) -> bool:
    """Busca imagenes realmente dibujadas, incluidas las inline y las de forms."""
    visited: set[int] = set()
    remaining = MAX_GRAPHIC_OBJECTS

    def visit(stream, resources, depth: int) -> bool:
        nonlocal remaining
        if depth > MAX_FORM_DEPTH or remaining <= 0:
            raise ExtractionFailedError("La inspeccion de imagenes PDF excedio sus limites.")
        remaining -= 1
        if stream is None:
            return False
        content = stream if isinstance(stream, ContentStream) else ContentStream(stream, page.pdf)
        objects = resources.get("/XObject", {})
        if hasattr(objects, "get_object"):
            objects = objects.get_object()
        for operands, operator in content.operations:
            if operator == b"INLINE IMAGE":
                return True
            if operator != b"Do" or not operands:
                continue
            reference = objects.get(operands[0])
            if reference is None:
                continue
            obj = reference.get_object()
            if obj.get("/Subtype") == "/Image":
                return True
            if obj.get("/Subtype") == "/Form" and id(obj) not in visited:
                visited.add(id(obj))
                nested = obj.get("/Resources", resources)
                if hasattr(nested, "get_object"):
                    nested = nested.get_object()
                if visit(obj, nested, depth + 1):
                    return True
        return False

    resources = page.get("/Resources", {})
    if hasattr(resources, "get_object"):
        resources = resources.get_object()
    return visit(page.get_contents(), resources, 0)


def graphics_only_pdf(page) -> bytes:
    """Copia una pagina y retira el texto digital solo de la copia para el OCR.

    Conserva posiciones, imagenes y trazos de tablas. El texto digital original
    se indexa por separado y nunca se sustituye por una reinterpretacion OCR.
    """
    writer = PdfWriter()
    writer.add_page(page)
    writer.remove_text()
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
