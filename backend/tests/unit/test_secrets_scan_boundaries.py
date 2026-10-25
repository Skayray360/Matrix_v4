# Creado por Aldo Garcia.
"""El escaner no debe convertir sus reportes en una fuga de credenciales."""

import json
from dataclasses import asdict

import pytest

from scripts.secrets_scan import _iter_files, scan


def test_long_synthetic_credential_is_redacted_before_excerpt_is_truncated(tmp_path):
    token = "synthetic-audit-token-" + "x" * 90
    (tmp_path / "config.py").write_text(f'api_key = "{token}"', encoding="utf-8")
    findings = scan(tmp_path)
    assert any(item.classification == "real" for item in findings)
    serialized = json.dumps([asdict(item) for item in findings])
    assert "synthetic-audit-token" not in serialized
    assert "[MATCH]" in serialized


def test_scanner_never_reads_file_symlinks_outside_the_audit_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = tmp_path / "external.py"
    # secrets-scan: allow (credencial sintetica para probar limites del escaner)
    target.write_text('password = "synthetic-outside-root"', encoding="utf-8")
    try:
        (root / "linked.py").symlink_to(target)
    except OSError:
        pytest.skip("Este sistema no permite crear enlaces simbolicos para la prueba")
    assert list(_iter_files(root)) == []
    assert scan(root) == []


def test_scanner_prunes_generated_directories_and_keeps_normal_source(tmp_path):
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "dependency.py").write_text("pass", encoding="utf-8")
    source = tmp_path / "app.py"
    source.write_text("pass", encoding="utf-8")
    assert list(_iter_files(tmp_path)) == [source]
