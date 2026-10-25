# Creado por Aldo Garcia.
"""Entrega limpia: corpus local intacto y ningun documento operativo distribuido."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from scripts.package_release import build_zip, iter_package_files, verify_release_layout


def test_zip_excluye_corpus_y_estado_sin_borrar_originales_locales(tmp_path: Path):
    source = tmp_path / "source"
    public = {
        "README.md", ".htaccess", "docker-compose.yml", "backend/config/env.example",
        "knowledge-base/.gitkeep", "frontend/dist/index.html",
    }
    private = {
        "reports/historico/revision.md", "reports/tests/junit.xml", "var/qdrant/storage.sqlite",
        "backend/config/.env", "backend/config/docker.env", "backend/config/docker.env.failed",
        "backend/config/mysql.ini", "backend/config/mysql-initialized.json",
        "backend/config/apache/matrix-rh.conf",
        "backend/config/secrets/local-admin-password.txt", "backend/runtime/python/python.exe",
        "backend/logs/backend.log", "knowledge-base/state/uploads/private.pdf",
        "knowledge-base/state/qdrant/storage.sqlite", "knowledge-base/models/blobs/hash",
        "knowledge-base/backups/backup.tar", "copia.zip", ".env", ".env.example",
        "backend/build/lib/app/main.py", "backend/other.egg-info/PKG-INFO",
        "knowledge-base/documents/prestaciones/original.pdf", "knowledge-base/unclassified/archivo.md",
        "knowledge-base/notas.txt", "knowledge-base/documents/README.txt",
    }
    for name in public | private:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    metadata = source / "backend/pyproject.toml"
    metadata.write_text('[project]\nversion="1.4.0"\n', encoding="utf-8")
    expected = public | {"backend/pyproject.toml"}
    selected = iter_package_files(source)
    assert not verify_release_layout(selected, source)
    assert {path.relative_to(source).as_posix() for path in selected} == expected
    archive = tmp_path / "release.zip"
    build_zip(source, archive, selected)
    with zipfile.ZipFile(archive) as result:
        root = "matrix-rh-1.4.0/"
        manifest_name = "backend/release/SHA256SUMS.txt"
        assert {name.removeprefix(root) for name in result.namelist()} == expected | {manifest_name}
        manifest_paths = set()
        for line in result.read(root + manifest_name).decode().splitlines():
            digest, name = line.split("  ", 1)
            manifest_paths.add(name)
            assert digest == hashlib.sha256(result.read(root + name)).hexdigest()
        original = "knowledge-base/documents/prestaciones/original.pdf"
        assert root + original not in result.namelist()
        assert original not in manifest_paths
    # Exclusiones de distribucion nunca eliminan archivos de una instalacion.
    assert all((source / name).is_file() for name in private)


@pytest.mark.parametrize("name", [
    "docs/guia.md", "windows/Install-MatrixRH.ps1", "scripts/old.sh", "data/original.pdf",
    "backend/README.md", "backend/scripts/extra.bat",
    "INSTALAR_MATRIX_RH.bat", "LICENSE",
])
def test_estructura_antigua_o_documentacion_extra_detiene_empaquetado_sin_borrar(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"original preserved")
    assert verify_release_layout(iter_package_files(tmp_path), tmp_path)
    assert path.read_bytes() == b"original preserved"


def test_zip_rechaza_documentos_operativos_aun_con_lista_explicita(tmp_path):
    private = tmp_path / "knowledge-base/documents/interno.pdf"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"synthetic private document")
    output = tmp_path / "delivery.zip"
    assert verify_release_layout([private], tmp_path)
    with pytest.raises(ValueError, match="corpus operativo"):
        build_zip(tmp_path, output, [private])
    assert not output.exists()
    assert private.read_bytes() == b"synthetic private document"
