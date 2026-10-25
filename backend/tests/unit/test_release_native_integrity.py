# Creado por Aldo Garcia.
"""Exercise the packaged source against the native installer integrity contract."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.package_release import build_zip, iter_package_files
from scripts.preflight import PreflightReport, check_integrity

ROOT = Path(__file__).resolve().parents[3]


def test_packaged_source_passes_native_guard_and_rejects_frontend_tampering(tmp_path: Path):
    powershell = os.environ.get("MATRIX_TEST_POWERSHELL") or shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required to execute the isolated release guard")

    archive = tmp_path / "source.zip"
    build_zip(ROOT, archive, iter_package_files(ROOT))
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as release:
        # The archive was created from this source; no external ZIP is extracted.
        release.extractall(extracted)
    installation, = extracted.iterdir()
    report = PreflightReport()
    check_integrity(report, root=installation, require_manifest=True)
    assert report.ok, report.as_dict()

    harness = tmp_path / "release-guard.ps1"
    harness.write_text(
        r"""# Creado por Aldo Garcia.
param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$script:Root=[IO.Path]::GetFullPath($Root)
$controller=Join-Path $script:Root 'backend/scripts/windows/MatrixRH.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($controller,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Controller AST parse failed' }
$guard=$ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-Release' },$false)
if ($null -eq $guard) { throw 'Release guard absent' }
Invoke-Expression $guard.Extent.Text
# Only the integrity function is loaded; no supervisor or service is executed.
Assert-Release -AllowFrontendBuild
$frontend=Join-Path $script:Root 'frontend/src/App.tsx'
[IO.File]::AppendAllText($frontend,"`n// synthetic tampering`n")
$failure=''
try { Assert-Release -AllowFrontendBuild } catch { $failure=$_.Exception.Message }
if ($failure -notmatch 'Integridad incorrecta: frontend/src/App\.tsx') {
    throw ('Frontend tampering did not fail the release guard: ' + $failure)
}
Write-Output 'SYNTHETIC PASS: generated release accepted; frontend tampering rejected'
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-File", str(harness), "-Root", str(installation)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SYNTHETIC PASS" in result.stdout

    # The Docker runtime carries compiled frontend assets, not its source tree.
    # Its backend preflight must still accept the native delivery manifest.
    for relative in ("frontend/src", "frontend/public"):
        directory = installation / relative
        if directory.is_dir():
            shutil.rmtree(directory)
    for relative in ("frontend/.npmrc", "frontend/vite.config.ts", "frontend/tsconfig.json", "frontend/index.html"):
        (installation / relative).unlink(missing_ok=True)
    runtime = PreflightReport()
    check_integrity(runtime, root=installation, require_manifest=True)
    assert runtime.ok, runtime.as_dict()

    backend = installation / "backend/app/main.py"
    backend.write_bytes(backend.read_bytes() + b"\n# synthetic tampering\n")
    modified = PreflightReport()
    check_integrity(modified, root=installation, require_manifest=True)
    assert not modified.ok
    assert any("backend/app/main.py" in check.detail for check in modified.checks if check.failed)
