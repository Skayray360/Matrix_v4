# Creado por Aldo Garcia.
"""Regresiones de paginas mixtas, secciones y titulos; sin servicios operativos."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.common.errors import ExtractionFailedError
from app.ingestion import loaders, local_ocr
from app.ingestion.local_ocr import OcrOptions
from app.ingestion.pdf_graphics import graphics_only_pdf, has_raster_images
from app.ingestion.service import IngestionService
from app.rag import index_manifest
from app.rag.chunking import chunk_blocks, split_into_blocks
from app.rag.embedding_prompts import document_embedding_title
from app.rag.schemas import Evidence
from app.rag.summary_scope import select_summary_scope

pytestmark = pytest.mark.unit

NATIVE = "Solo personal de planta. Prima del 7.5%."
OCR_TABLE = "Antiguedad    Dias    Prima\n1 a 4         12      7.5\n5 a 9         18      13.5"


def mixed_pdf(*, image=True, form=False, inline=False, unused_image=False, second_page=False) -> bytes:
    writer = PdfWriter()
    if second_page:
        writer.add_blank_page(width=400, height=300)
    page = writer.add_blank_page(width=400, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    page[NameObject("/Resources")] = resources
    body = f"BT /F1 12 Tf 20 260 Td ({NATIVE}) Tj ET\n".encode()
    if image:
        raster = DecodedStreamObject()
        raster.set_data(b"\xff" * (20 * 10))
        raster.update({
            NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(20), NameObject("/Height"): NumberObject(10),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8),
        })
        objects = DictionaryObject({NameObject("/Im"): raster})
        if form:
            nested = DecodedStreamObject()
            nested.set_data(b"/Im Do")
            nested.update({
                NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/Resources"): DictionaryObject({NameObject("/XObject"): objects}),
            })
            objects = DictionaryObject({NameObject("/Fm"): nested})
        resources[NameObject("/XObject")] = objects
        if not unused_image:
            body += b"q 200 0 0 100 20 20 cm " + (b"/Fm Do" if form else b"/Im Do") + b" Q\n"
    if inline:
        body += b"q 1 0 0 1 20 20 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \xff EI Q\n"
    content = DecodedStreamObject()
    content.set_data(body)
    page[NameObject("/Contents")] = content
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("options", [{"image": True}, {"form": True}, {"image": False, "inline": True}])
def test_image_detection_covers_drawn_xobjects_forms_and_inline(options):
    page = PdfReader(io.BytesIO(mixed_pdf(**options))).pages[0]
    assert has_raster_images(page)


@pytest.mark.parametrize("options", [{"image": False}, {"unused_image": True}])
def test_text_and_unused_image_resources_do_not_trigger_ocr(options):
    page = PdfReader(io.BytesIO(mixed_pdf(**options))).pages[0]
    assert not has_raster_images(page)


def test_graphics_copy_keeps_images_and_never_changes_original_text():
    page = PdfReader(io.BytesIO(mixed_pdf())).pages[0]
    original = page.extract_text()
    graphics = PdfReader(io.BytesIO(graphics_only_pdf(page)))
    assert len(graphics.pages) == 1
    assert not graphics.pages[0].extract_text().strip()
    assert has_raster_images(graphics.pages[0])
    assert page.extract_text() == original
    assert original.strip() == NATIVE


def test_mixed_page_without_ocr_preserves_text_and_warns(monkeypatch):
    monkeypatch.setattr(loaders, "ocr_pdf_page", lambda *_a, **_k: pytest.fail("OCR desactivado"))
    result = loaders.extract_pdf(mixed_pdf())
    assert result.plain_text() == NATIVE
    assert any("texto e imagenes" in warning and "1" in warning for warning in result.warnings)
    assert any("tablas" in warning for warning in result.warnings)


def test_mixed_ocr_complements_native_text_and_preserves_rows_and_page(monkeypatch):
    calls = []

    def recognize(data, page_number, options, *, preserve_layout=False):
        calls.append((page_number, preserve_layout))
        document = PdfReader(io.BytesIO(data))
        assert len(document.pages) == 1
        assert not document.pages[0].extract_text().strip()
        return OCR_TABLE

    monkeypatch.setattr(loaders, "ocr_pdf_page", recognize)
    result = loaders.extract_pdf(mixed_pdf(), ocr_options=OcrOptions(enabled=True))
    assert calls == [(1, True)]
    assert NATIVE in result.plain_text()
    assert OCR_TABLE in result.plain_text()
    assert {block.page_or_sheet for block in result.blocks} == {"pagina 1"}
    chunks = chunk_blocks(result.blocks, chunk_size_tokens=900, overlap_tokens=120)
    assert len(chunks) == 1 and NATIVE in chunks[0].text and OCR_TABLE in chunks[0].text
    assert any("revisar cifras, filas y columnas" in warning for warning in result.warnings)


def test_graphics_copy_page_one_does_not_change_source_page_citation(monkeypatch):
    def recognize(data, number, options, **kwargs):
        return OCR_TABLE if kwargs.get("preserve_layout") else ""

    monkeypatch.setattr(loaders, "ocr_pdf_page", recognize)
    result = loaders.extract_pdf(mixed_pdf(second_page=True), ocr_options=OcrOptions(enabled=True))
    assert {block.page_or_sheet for block in result.blocks} == {"pagina 2"}


@pytest.mark.parametrize("failure", [ExtractionFailedError("Falta Tesseract."), RuntimeError("ruta privada")])
def test_mixed_ocr_failure_keeps_original_and_reports_incomplete_coverage(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(loaders, "ocr_pdf_page", fail)
    result = loaders.extract_pdf(mixed_pdf(), ocr_options=OcrOptions(enabled=True))
    assert result.plain_text() == NATIVE
    assert any("Cobertura de imagenes pendiente" in warning for warning in result.warnings)
    assert "ruta privada" not in str(result.warnings)


def test_empty_graphic_ocr_does_not_claim_complete_extraction(monkeypatch):
    monkeypatch.setattr(loaders, "ocr_pdf_page", lambda *_a, **_k: "")
    result = loaders.extract_pdf(mixed_pdf(), ocr_options=OcrOptions(enabled=True))
    assert result.plain_text() == NATIVE
    assert any("no produjo texto" in warning for warning in result.warnings)


def test_existing_searchable_layer_is_not_duplicated(monkeypatch):
    monkeypatch.setattr(loaders, "ocr_pdf_page", lambda *_a, **_k: NATIVE)
    result = loaders.extract_pdf(mixed_pdf(), ocr_options=OcrOptions(enabled=True))
    assert result.plain_text().count(NATIVE) == 1


@pytest.mark.parametrize("section", ["", "pagina 8", "Página 8", "parrafo 2", "tabla 3", "diapositiva 5"])
def test_embedding_title_uses_document_instead_of_generic_location(section):
    assert document_embedding_title(filename="Plan 2026.pdf", section=section) == "Plan 2026.pdf"


def test_embedding_title_keeps_document_and_real_section():
    assert document_embedding_title(filename="Plan 2026.pdf", section="Portabilidad") == "Plan 2026.pdf — Portabilidad"


def test_ingestion_embeds_document_identity_without_changing_citable_text():
    captured = []
    agent = IngestionService.__new__(IngestionService)
    agent._llm = SimpleNamespace(embed=lambda inputs: captured.extend(inputs) or [[0.1] * 768 for _ in inputs])
    chunk = SimpleNamespace(text=NATIVE, metadata=SimpleNamespace(filename="Plan 2026.pdf", section="pagina 8"))
    assert len(agent._embed_chunks([chunk])) == 1
    assert captured == [f"title: Plan 2026.pdf | text: {NATIVE}"]
    assert chunk.text == NATIVE and chunk.metadata.section == "pagina 8"


@pytest.mark.parametrize("marker", ["🔹 CHUNK 1 — Ahorro", "CHUNK 1 - Ahorro", "CHUNK 1: Ahorro"])
def test_explicit_pdf_sections_separate_short_topics_with_literal_text(marker):
    text = f"{marker}\nAportacion del 5%.\n\n🔹 CHUNK 2 — Bono\nSe entrega en diciembre."
    blocks = [replace(block, page_or_sheet="pagina 2") for block in split_into_blocks(text, document_headings=True)]
    chunks = chunk_blocks(blocks, chunk_size_tokens=900, overlap_tokens=120)
    assert [chunk.section for chunk in chunks] == ["Ahorro", "Bono"]
    assert [chunk.page_or_sheet for chunk in chunks] == ["pagina 2", "pagina 2"]
    assert chunks[0].text.startswith(marker)
    assert "diciembre" not in chunks[0].text and "5%" not in chunks[1].text


def test_unmarked_text_and_default_markdown_splitter_keep_their_contract():
    text = "Se menciona CHUNK 1 — Ahorro en el documento."
    assert all(block.kind == "paragraph" for block in split_into_blocks(text, document_headings=True))
    assert all(block.kind == "paragraph" for block in split_into_blocks("CHUNK 1 — Ahorro\nTexto."))


def test_summary_keeps_continuation_but_excludes_other_named_section_on_same_page():
    def evidence(index, text, section):
        return Evidence(
            source_id=f"prestaciones/plan.pdf#{index}", text=text, score=1,
            category="prestaciones", filename="plan.pdf", section=section, page_or_sheet="pagina 2",
            document_id="plan", chunk_id=str(index),
        )

    saving = evidence(0, "Ahorro\nAportacion del 5%.", "Ahorro")
    continuation = evidence(1, "Sujeto a autorizacion.", "pagina 2")
    bonus = evidence(2, "Bono\nSe paga en diciembre.", "Bono")
    result = select_summary_scope("Resume la seccion de ahorro.", (saving, continuation, bonus))
    assert result.evidences == (saving, continuation)


def test_pipeline_revision_invalidates_old_vectors_for_unchanged_bytes(monkeypatch):
    current = index_manifest.indexing_fingerprint()
    monkeypatch.setattr(index_manifest, "PIPELINE_VERSION", 8)
    assert index_manifest.indexing_fingerprint() != current


@pytest.mark.parametrize("pattern", ["PLATICA DE PRESTACIONES*.pdf", "*PENSIONES*.pdf"])
def test_supplied_pdf_image_tables_are_reported_as_unverified_when_ocr_is_disabled(pattern):
    root = Path(__file__).resolve().parents[3] / "knowledge-base" / "documents"
    path = next(root.rglob(pattern))
    document = PdfReader(path)
    assert has_raster_images(document.pages[7])
    result = loaders.extract_pdf(path.read_bytes())
    assert any("texto e imagenes" in warning and "8" in warning for warning in result.warnings)
    assert any(block.page_or_sheet == "pagina 8" for block in result.blocks)


@pytest.mark.parametrize("preserve_layout", [False, True])
def test_local_ocr_uses_auto_segmentation_and_preserves_columns_only_when_requested(monkeypatch, preserve_layout):
    commands = []
    monkeypatch.setattr(local_ocr.shutil, "which", lambda name: f"/tools/{name}")

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] <= 30 and kwargs["check"]
        output = b"List of available languages:\nspa\n" if command[-1] == "--list-langs" else OCR_TABLE.encode()
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(local_ocr.subprocess, "run", run)
    assert local_ocr.ocr_pdf_page(b"pdf", 2, OcrOptions(), preserve_layout=preserve_layout) == OCR_TABLE
    renderer, recognizer = commands[-2:]
    assert renderer[renderer.index("-f") + 1] == "2"
    assert renderer[renderer.index("-l") + 1] == "2"
    assert renderer[renderer.index("-scale-to") + 1] == "3500"
    assert recognizer[recognizer.index("--psm") + 1] == "3"
    assert ("preserve_interword_spaces=1" in recognizer) is preserve_layout
