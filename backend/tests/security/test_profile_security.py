# Creado por Aldo Garcia.
"""Superficie API y diagnostico de cifrado por perfil, sin servicios reales."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import AppEnv, get_settings
from scripts import preflight

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.mark.parametrize("install_mode", ["windows_local", "docker_local", "DOCKER_LOCAL"])
@pytest.mark.parametrize("environment", list(AppEnv))
@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
def test_documentation_endpoints_exist_only_in_development_and_test(monkeypatch, environment, path, install_mode):
    settings = get_settings().model_copy(update={"app_env": environment, "matrix_install_mode": install_mode})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    # No lifespan: esta prueba HTTP no arranca scheduler, SQL ni modelos.
    client = TestClient(main.create_app(), base_url="http://127.0.0.1")
    response = client.get(path)
    exposed = environment in {AppEnv.DEVELOPMENT, AppEnv.TEST} and install_mode.casefold() != "docker_local"
    assert response.status_code == (200 if exposed else 404)
    if path.endswith(".json") and exposed:
        assert "/api/v1/chat/submit" in response.json()["paths"]
    if not exposed:
        assert "openapi" not in response.text.lower()
        assert "swagger" not in response.text.lower()


def standalone_settings(tmp_path):
    return SimpleNamespace(
        app_env="standalone", upload_storage_path=tmp_path / "uploads",
        knowledge_root_path=tmp_path / "knowledge", qdrant_storage_path=tmp_path / "qdrant",
        qdrant_mode="embedded", database_datadir_path=tmp_path,
    )


@pytest.mark.parametrize(("protection", "status", "percentage", "expected"), [
    ("On", "FullyEncrypted", 100, preflight.OK),
    ("Off", "FullyEncrypted", 100, preflight.WARN),
    ("On", "EncryptionInProgress", 50, preflight.WARN),
])
def test_standalone_measures_all_local_paths_without_blocking_startup(
    tmp_path, monkeypatch, protection, status, percentage, expected,
):
    settings = standalone_settings(tmp_path)
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    observed = []

    def probe(paths):
        observed.extend(paths)
        return [{"mount": "C:" if index % 2 else "D:", "protection": protection,
                 "status": status, "percentage": percentage} for index, _ in enumerate(paths)]

    monkeypatch.setattr(preflight, "windows_encryption_status", probe)
    report = preflight.PreflightReport()
    preflight.check_storage_encryption(report, settings)
    result = report.checks[-1]
    assert observed == [preflight.PROJECT_ROOT, settings.upload_storage_path, settings.knowledge_root_path,
                        settings.qdrant_storage_path, settings.database_datadir_path]
    assert result.status == expected and result.required is False and report.ok
    assert "respaldo no fue verificado" in result.detail
    if expected == preflight.WARN:
        assert "C:" in result.detail and "D:" in result.detail


def test_standalone_unknown_mysql_does_not_prevent_probe_of_other_paths(tmp_path, monkeypatch):
    settings = standalone_settings(tmp_path)
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    mysql = Mock(side_effect=AssertionError("No consultar MySQL que ya fallo"))
    monkeypatch.setattr(preflight, "_mysql_datadir", mysql)
    probe = Mock(return_value=[{"mount": "C:", "status": "FullyEncrypted", "protection": "On", "percentage": 100}])
    monkeypatch.setattr(preflight, "windows_encryption_status", probe)
    report = preflight.PreflightReport()
    preflight.check_storage_encryption(report, settings, database_ok=False)
    mysql.assert_not_called()
    probe.assert_called_once()
    assert report.checks[-1].status == preflight.WARN and report.ok
    assert "datadir MySQL no verificado" in report.checks[-1].detail


def test_standalone_bitlocker_error_does_not_reveal_error_content(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    monkeypatch.setattr(preflight, "windows_encryption_status", Mock(side_effect=OSError("private-fixture-error")))
    report = preflight.PreflightReport()
    preflight.check_storage_encryption(report, standalone_settings(tmp_path))
    assert report.checks[-1].status == preflight.WARN and report.ok
    assert "no verificable" in report.checks[-1].detail
    assert "private-fixture-error" not in preflight.render_text(report)


def test_standalone_on_linux_does_not_claim_bitlocker_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")
    probe = Mock(side_effect=AssertionError("No BitLocker fuera de Windows"))
    monkeypatch.setattr(preflight, "windows_encryption_status", probe)
    report = preflight.PreflightReport()
    preflight.check_storage_encryption(report, standalone_settings(tmp_path))
    probe.assert_not_called()
    assert report.checks[-1].status == preflight.WARN and report.ok
    assert "cifrado no verificado" in report.checks[-1].detail


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_profiles_do_not_probe_host_encryption(tmp_path, monkeypatch, environment):
    settings = standalone_settings(tmp_path)
    settings.app_env = environment
    probe = Mock(side_effect=AssertionError("No sondear el host de desarrollo"))
    monkeypatch.setattr(preflight, "windows_encryption_status", probe)
    report = preflight.PreflightReport()
    preflight.check_storage_encryption(report, settings)
    probe.assert_not_called()
    assert report.checks[-1].required is False and report.ok
