# Creado por Aldo Garcia.
"""Guardia de volumen con sondas simuladas; no acredita BitLocker real."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import installation_backup as backup
from tests.unit import test_installation_backup as backup_fixtures

pytestmark = [pytest.mark.unit, pytest.mark.security]
installation = backup_fixtures.installation
no_services = backup_fixtures.no_services


def volume_status(path, *, mount="D:", protection="On", status="FullyEncrypted", percentage=100):
    return [{"path": str(path), "mount": mount, "protection": protection,
             "status": status, "percentage": percentage}]


@pytest.mark.parametrize("mount", ["C:", "D:"])
def test_guard_measures_destination_before_creation_and_records_only_creation_time(
    installation, tmp_path, monkeypatch, mount,
):
    destination = tmp_path / "verified"
    observed = []
    monkeypatch.setattr(backup.platform, "system", lambda: "Windows")

    def probe(paths):
        assert paths == [destination]
        assert not destination.exists()
        observed.extend(paths)
        return volume_status(destination, mount=mount)

    monkeypatch.setattr(backup, "windows_encryption_status", probe)
    result = backup.create_backup(installation, destination, require_encrypted_destination=True)
    verified = backup.verify_backup(destination)
    assert observed == [destination]
    assert verified["destination_volume_check"] == result["destination_volume_check"]
    check = result["destination_volume_check"]
    assert check["status"] == "verified_at_creation" and check["mount"] == mount
    assert check["file_encryption"] == "none; volume_protection_is_not_portable"
    assert check["checked_at_utc"]
    # El archivo sigue siendo legible: esta opcion protege el volumen, no
    # crea un contenedor cifrado que pueda moverse a un disco no protegido.
    copied = destination / backup.PAYLOAD / "knowledge-base/state/mysql/mysql.ibd"
    assert copied.read_bytes() == b"SYNTHETIC MYSQL DATADIR, NOT A REAL DB"


@pytest.mark.parametrize("status", [
    {"protection": "Off"}, {"status": "EncryptionInProgress", "percentage": 50},
    {"percentage": 99}, {"mount": ""},
])
def test_unprotected_or_unidentified_destination_is_rejected_before_any_copy(
    installation, tmp_path, monkeypatch, status,
):
    destination = tmp_path / "rejected"
    monkeypatch.setattr(backup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(backup, "windows_encryption_status", lambda paths: volume_status(paths[0], **status))
    copy = Mock(side_effect=AssertionError("No debe comenzar a copiar"))
    monkeypatch.setattr(backup, "_copy_file", copy)
    with pytest.raises(backup.BackupError, match="requiere BitLocker"):
        backup.create_backup(installation, destination, require_encrypted_destination=True)
    copy.assert_not_called()
    assert not destination.exists()


def test_unknown_bitlocker_does_not_create_default_backup_root_or_expose_probe_output(
    installation, monkeypatch,
):
    monkeypatch.setattr(backup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(backup, "windows_encryption_status", Mock(side_effect=OSError("PRIVATE_PROBE_FIXTURE")))
    with pytest.raises(backup.BackupError, match="No se pudo verificar BitLocker") as error:
        backup.create_backup(installation, require_encrypted_destination=True)
    assert "PRIVATE_PROBE_FIXTURE" not in str(error.value)
    assert not (installation / "knowledge-base/backups").exists()


def test_required_encryption_fails_closed_on_linux_and_default_keeps_prior_behavior(
    installation, tmp_path, monkeypatch,
):
    monkeypatch.setattr(backup.platform, "system", lambda: "Linux")
    probe = Mock(side_effect=AssertionError("No se debe ejecutar PowerShell"))
    monkeypatch.setattr(backup, "windows_encryption_status", probe)
    destination = tmp_path / "rejected"
    with pytest.raises(backup.BackupError, match="requiere Windows"):
        backup.create_backup(installation, destination, require_encrypted_destination=True)
    assert not destination.exists()
    result = backup.create_backup(installation, tmp_path / "ordinary")
    assert result["destination_volume_check"] == {"status": "not_checked"}
    probe.assert_not_called()


@pytest.mark.parametrize("path_kind", ["unc", "symlink", "junction"])
def test_indirect_or_remote_destination_is_rejected_before_volume_probe(
    installation, tmp_path, monkeypatch, path_kind,
):
    probe = Mock(side_effect=AssertionError("No se debe confiar en una ruta indirecta"))
    monkeypatch.setattr(backup, "windows_encryption_status", probe)
    if path_kind == "unc":
        destination = Path("//synthetic-server/share/new")
    else:
        indirect = tmp_path / "indirect"
        if path_kind == "symlink":
            try:
                indirect.symlink_to(tmp_path, target_is_directory=True)
            except OSError:
                pytest.skip("El sistema no permite enlaces de prueba")
        else:
            indirect.mkdir()
            original = Path.lstat

            def lstat(path):
                result = original(path)
                if path == indirect:
                    from types import SimpleNamespace

                    return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
                return result

            monkeypatch.setattr(Path, "lstat", lstat)
        destination = indirect / "new"
    with pytest.raises(backup.BackupError, match="red|enlaces|junctions"):
        backup.create_backup(installation, destination, require_encrypted_destination=True)
    probe.assert_not_called()


def test_cli_requires_destination_encryption_only_when_requested(installation, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backup.platform, "system", lambda: "Linux")
    destination = tmp_path / "rejected"
    assert backup.main(["backup", "--project-root", str(installation), "--destination", str(destination),
                        "--require-encrypted-destination"]) == 1
    assert "requiere Windows" in capsys.readouterr().out
    assert not destination.exists()
