# Creado por Aldo Garcia.
"""Evita metadata activa cruzada sin importar app ni alterar auditorias pasadas."""

import json
from pathlib import Path

import pytest

from scripts.verify_documentation import check_release_versions

pytestmark = pytest.mark.unit


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def release(tmp_path):
    files = {
        "backend/pyproject.toml": '[project]\nname="matrix-rh-backend"\nversion="1.4.2"',
        "backend/app/__init__.py": '__version__ = "1.4.2"\nraise RuntimeError("must not import")',
        "backend/uv.lock": '[[package]]\nname="matrix-rh-backend"\nversion="1.4.2"',
        "frontend/package.json": '{"version":"1.4.2"}',
        "frontend/package-lock.json": '{"version":"1.4.2","packages":{"":{"version":"1.4.2"}}}',
        "backend/config/model-manifest.json": '{"release":"1.4.2","evidence":"not_verified"}',
        "backend/config/runtime-manifest.json": '{"python":{"version":"3.12.16"},"node":{"version":"24.22.1"}}',
        "backend/Dockerfile": "FROM node:24.22.1-alpine AS frontend\nFROM python:3.12.16-slim AS runtime\n",
        "backend/release/historical.json": '{"version":"0.1.0","tests_passed":14}',
    }
    for relative, content in files.items():
        write(tmp_path, relative, content)
    return tmp_path


def test_consistent_metadata_does_not_import_code_or_reattribute_historical_evidence(release):
    before = {str(path): path.read_bytes() for path in release.rglob("*") if path.is_file()}
    assert check_release_versions(release) == []
    assert {str(path): path.read_bytes() for path in release.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("relative", [
    "backend/app/__init__.py", "backend/uv.lock", "frontend/package.json",
    "frontend/package-lock.json", "backend/config/model-manifest.json",
])
def test_previous_release_metadata_is_rejected(release, relative):
    path = release / relative
    path.write_text(path.read_text().replace("1.4.2", "1.3.3"), encoding="utf-8")
    errors = check_release_versions(release)
    assert len(errors) == 1 and relative in errors[0]


def test_npm_nested_project_version_cannot_diverge(release):
    path = release / "frontend/package-lock.json"
    lock = json.loads(path.read_text())
    lock["packages"][""]["version"] = "1.3.3"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert any("package-lock.json" in error for error in check_release_versions(release))


@pytest.mark.parametrize("old,new", [("3.12.16", "3.12.15"), ("24.22.1", "22.0.0")])
def test_docker_runtime_version_drift_is_rejected(release, old, new):
    path = release / "backend/Dockerfile"
    path.write_text(path.read_text().replace(old, new), encoding="utf-8")
    assert any("Dockerfile" in error for error in check_release_versions(release))


def test_unreadable_metadata_fails_closed_without_echoing_content(release):
    write(release, "backend/config/model-manifest.json", "synthetic-private-invalid-json")
    assert check_release_versions(release) == ["Metadata de version no verificable (JSONDecodeError)."]
