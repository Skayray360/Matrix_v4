# Creado por Aldo Garcia.
"""Errores DACL observables solo sobre fixtures sinteticos del harness."""

from types import SimpleNamespace

import pytest

from scripts import verify_chunk_export_acl as harness

pytestmark = pytest.mark.unit


def test_real_function_failure_identifies_stage_and_captures_synthetic_stderr(tmp_path, monkeypatch):
    fixture = tmp_path / "synthetic '$() file.json"
    fixture.write_text("synthetic")
    calls = []

    def fail(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic PowerShell error " * 400)

    monkeypatch.setattr(harness.subprocess, "run", fail)
    diagnostic = harness.SyntheticSubprocess({fixture})
    diagnostic.stage = "protect_file"
    actual_function = harness.load_acl_function(diagnostic)
    with pytest.raises(harness.AclVerificationError) as failure:
        actual_function(fixture)
    report = failure.value.report
    assert report["stage"] == "protect_file" and report["returncode"] == 1
    assert report["synthetic_stderr"].startswith("synthetic PowerShell error")
    assert len(report["synthetic_stderr"]) == 6000
    assert calls[0][1]["capture_output"] and calls[0][1]["text"]
    assert "stdout" not in calls[0][1] and "stderr" not in calls[0][1]
    assert str(fixture) not in calls[0][0][-1]


def test_proxy_rejects_nonfixture_paths_before_executing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(harness.subprocess, "run", lambda *_a, **_k: calls.append(True))
    diagnostic = harness.SyntheticSubprocess({tmp_path / "allowed.json"})
    with pytest.raises(ValueError, match="sinteticas"):
        diagnostic.run(["unused"], env={"MATRIX_REVIEW_FILE": str(tmp_path / "other.json")})
    assert calls == []


def test_proxy_preserves_success_output_for_verification(tmp_path, monkeypatch):
    fixture = tmp_path / "child.json"
    expected = SimpleNamespace(returncode=0, stdout="PRIVATE_ACL_VERIFIED\n", stderr="")
    monkeypatch.setattr(harness.subprocess, "run", lambda *_a, **_k: expected)
    diagnostic = harness.SyntheticSubprocess({fixture})
    result = diagnostic.run(["unused"], env={"MATRIX_ACL_CHILD": str(fixture)},
                            capture_output=True, text=True, check=True)
    assert result is expected
