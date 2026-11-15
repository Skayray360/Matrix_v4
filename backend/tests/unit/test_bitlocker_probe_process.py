# Creado por Aldo Garcia.
"""Contrato del proceso de sonda; no acredita BitLocker de un volumen real."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import preflight


@pytest.mark.parametrize("module_variable", ["PSMODULEPATH", "PSModulePath", "psmodulepath"])
def test_bitlocker_probe_isolates_modules_and_decodes_unicode(tmp_path, monkeypatch, module_variable):
    for key in tuple(os.environ):
        if key.upper() == "PSMODULEPATH":
            monkeypatch.delenv(key)
    monkeypatch.setenv(module_variable, "synthetic-pwsh7-modules")
    monkeypatch.setenv("MATRIX_PROBE_SYNTHETIC", "preserved")
    paths = [tmp_path / "Nómina" / "東京"]
    payload = [{"path": str(paths[0]), "mount": "C:", "status": "FullyEncrypted",
                "protection": "On", "percentage": 100}]
    real_run = subprocess.run
    environments = []

    def run(command, **kwargs):
        assert Path(command[0]).name == "powershell.exe"
        assert command[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
        assert "OutputEncoding" in command[4] and "System.Text.UTF8Encoding" in command[4]
        assert json.loads(kwargs["input"]) == [str(path) for path in paths]
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == 30 and kwargs["check"] is False
        environments.append(kwargs["env"])
        # Solo el productor se sustituye: subprocess decodifica bytes UTF-8
        # reales y windows_encryption_status valida las rutas Unicode devueltas.
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        script = "import sys; sys.stdout.buffer.write(" + repr(encoded) + ")"
        return real_run([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.windows_encryption_status(paths) == payload
    assert not any(key.upper() == "PSMODULEPATH" for key in environments[0])
    assert environments[0]["MATRIX_PROBE_SYNTHETIC"] == "preserved"
    assert os.environ[module_variable] == "synthetic-pwsh7-modules"
    assert os.environ["MATRIX_PROBE_SYNTHETIC"] == "preserved"
