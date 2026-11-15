# Creado por Aldo Garcia.
"""Evidencia ejecutable de las excepciones SAST limitadas a XML y OCR locales."""

import io
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from app.common.errors import ExtractionFailedError, UnsupportedFileError
from app.ingestion import local_ocr, pptx
from app.security import upload_guard

pytestmark = pytest.mark.security


def office_archive(xml: bytes) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", xml)
        archive.writestr("word/document.xml", b"<synthetic/>")
    return stream.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE Types [<!ENTITY x SYSTEM "file:///synthetic-not-readable">]><Types>&x;</Types>',
    '<!ENTITY x SYSTEM "file:///synthetic-not-readable"><Types/>',
])
@pytest.mark.parametrize("parser", ["pptx", "upload"])
def test_xml_declarations_never_reach_parser_in_supported_byte_orders(monkeypatch, encoding, declaration, parser):
    xml = declaration.encode(encoding)
    monkeypatch.setattr(pptx.ElementTree, "fromstring", lambda *_: pytest.fail("Untrusted declaration reached parser"))
    data = office_archive(xml)
    if parser == "pptx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive, pytest.raises(ExtractionFailedError, match="declaraciones"):
            pptx._xml(archive, "[Content_Types].xml")
    else:
        with pytest.raises(UnsupportedFileError, match="declaraciones"):
            upload_guard._check_ooxml_bomb(data, ".docx")


def test_pptx_xml_size_is_rejected_before_read_or_parse(monkeypatch):
    monkeypatch.setattr(pptx.ElementTree, "fromstring", lambda *_: pytest.fail("Oversized XML reached parser"))
    archive = SimpleNamespace(
        getinfo=lambda _: SimpleNamespace(file_size=pptx.MAX_XML_BYTES + 1),
        read=lambda _: pytest.fail("Oversized XML was decompressed"),
    )
    with pytest.raises(ExtractionFailedError, match="limite"):
        pptx._xml(archive, "synthetic.xml")


def test_upload_manifest_size_is_rejected_before_parse(monkeypatch):
    monkeypatch.setattr(upload_guard.ElementTree, "fromstring", lambda *_: pytest.fail("Oversized XML reached parser"))
    data = office_archive(b"x" * (1024 * 1024 + 1))
    with pytest.raises(UnsupportedFileError, match="manifiesto Office excede"):
        upload_guard._check_ooxml_bomb(data, ".docx")


def test_ocr_runs_only_resolved_fixed_binaries_without_shell_and_with_limits(monkeypatch, tmp_path):
    resolved = {name: str(tmp_path / "tools with spaces" / name) for name in ("pdftoppm", "tesseract")}
    looked_up = []
    commands = []

    def which(name):
        looked_up.append(name)
        return resolved[name]

    def run(command, **kwargs):
        assert isinstance(command, list)
        assert command[0] in resolved.values()
        assert kwargs.get("shell", False) is False
        assert kwargs["check"] is True and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] in {5, 30}
        commands.append((command, kwargs))
        return SimpleNamespace(stdout=b"spa\neng\n" if command[-1] == "--list-langs" else b"recognized text")

    monkeypatch.setattr(local_ocr.shutil, "which", which)
    monkeypatch.setattr(local_ocr.subprocess, "run", run)
    assert local_ocr.ocr_pdf_page(b"inert PDF", 2, local_ocr.OcrOptions(language="spa+eng")) == "recognized text"
    assert looked_up == ["pdftoppm", "tesseract"]
    assert len(commands) == 3
    assert commands[0][0] == [resolved["tesseract"], "--list-langs"]
    assert [kwargs["timeout"] for _, kwargs in commands] == [5, 30, 30]
    renderer, recognizer = commands[1][0], commands[2][0]
    assert renderer[0] == resolved["pdftoppm"]
    assert renderer[renderer.index("-scale-to") + 1] == "3500"
    assert renderer[renderer.index("-f") + 1] == renderer[renderer.index("-l") + 1] == "2"
    assert recognizer[0] == resolved["tesseract"]
    assert recognizer[recognizer.index("-l") + 1] == "spa+eng"


@pytest.mark.parametrize("language", ["spa;command", "spa --outside", "spa\neng", "$(command)"])
def test_ocr_language_cannot_add_arguments_or_shell_syntax(language):
    with pytest.raises(ExtractionFailedError, match="configuracion"):
        local_ocr.OcrOptions(language=language)


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_each_ocr_process_timeout_fails_closed_without_private_output(monkeypatch, stage):
    monkeypatch.setattr(local_ocr.shutil, "which", lambda name: f"/tools/{name}")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == stage:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"synthetic-private-text")
        return SimpleNamespace(stdout=b"spa\n")

    monkeypatch.setattr(local_ocr.subprocess, "run", run)
    with pytest.raises(ExtractionFailedError, match="dentro de sus limites") as caught:
        local_ocr.ocr_pdf_page(b"inert PDF", 1, local_ocr.OcrOptions())
    assert len(calls) == stage and "synthetic-private-text" not in str(caught.value)
