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


def test_scanner_checks_minified_lines_and_every_candidate(tmp_path):
    token = "synthetic-audit-token-" + "y" * 30
    content = 'const padding="' + "x" * 8000 + '";api_key="replace_me";api_key="' + token + '";'
    (tmp_path / "bundle.js").write_text(content, encoding="utf-8")
    findings = scan(tmp_path)
    assert [item.classification for item in findings if item.rule == "generic_api_key"] == ["real"]
    assert token not in json.dumps([asdict(item) for item in findings])


def test_placeholder_before_another_password_does_not_hide_it(tmp_path):
    token = "synthetic-audit-token-" + "z" * 30
    content = 'password="replace_me"; password="' + token + '"'
    (tmp_path / "config.js").write_text(content, encoding="utf-8")
    findings = scan(tmp_path)
    assert [item.classification for item in findings] == ["placeholder", "real"]


def test_example_filename_does_not_allowlist_unknown_credentials(tmp_path):
    token = "synthetic-audit-token-" + "q" * 30
    (tmp_path / ".env.example").write_text('api_key="' + token + '"', encoding="utf-8")
    assert any(item.classification == "real" for item in scan(tmp_path))


def test_nested_private_dotenv_is_not_distributable_even_without_a_matching_key(tmp_path):
    config = tmp_path / "backend" / "config"
    config.mkdir(parents=True)
    (config / ".env").write_text("PRIVATE_SETTING=synthetic-value\n", encoding="utf-8")
    assert any(item.rule == "env_file_present" for item in scan(tmp_path))
