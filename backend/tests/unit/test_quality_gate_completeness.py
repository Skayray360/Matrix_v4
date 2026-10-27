# Creado por Aldo Garcia.
"""El gate no aprueba pasos obligatorios omitidos ni usa evidencia obsoleta."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_quality_gate as gate


@pytest.mark.parametrize(
    ("status", "blocking", "expected", "code"),
    [
        (gate.SKIPPED, True, gate.INCOMPLETE, 2),
        (gate.INCOMPLETE, True, gate.INCOMPLETE, 2),
        (gate.FAIL, True, gate.FAIL, 1),
        (gate.SKIPPED, False, gate.PASS, 0),
        (gate.PASS, True, gate.PASS, 0),
    ],
)
def test_verdict_distinguishes_missing_required_evidence(tmp_path, monkeypatch, status, blocking, expected, code):
    monkeypatch.setattr(gate, "build_gates", lambda _args: [gate.GateResult("synthetic", status, blocking=blocking)])
    output = tmp_path / "result.json"
    assert gate.main(["--output", str(output)]) == code
    assert json.loads(output.read_text())["overall"] == expected


@pytest.mark.parametrize(("count", "skipped", "expected"), [(10, 2, gate.INCOMPLETE), (0, 0, gate.INCOMPLETE), (10, 0, gate.PASS)])
def test_junit_zero_exit_does_not_hide_skipped_cases(tmp_path, monkeypatch, count, skipped, expected):
    report = tmp_path / "junit.xml"

    def run(*_args, **_kwargs):
        report.write_text(f'<testsuites><testsuite tests="{count}" skipped="{skipped}"/></testsuites>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    result = gate._run(["synthetic"], cwd=tmp_path, name="integration", require_complete_junit=report)
    assert result.status == expected


def test_never_accepts_a_previous_junit_report(tmp_path, monkeypatch):
    report = tmp_path / "junit.xml"
    report.write_text('<testsuites><testsuite tests="10" skipped="0"/></testsuites>')
    monkeypatch.setattr(gate.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    result = gate._run(["synthetic"], cwd=tmp_path, name="integration", require_complete_junit=report)
    assert result.status == gate.FAIL


def test_failure_takes_precedence_over_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "build_gates", lambda _args: [
        gate.GateResult("missing", gate.SKIPPED), gate.GateResult("broken", gate.FAIL),
    ])
    assert gate.main(["--output", str(tmp_path / "result.json")]) == 1


def test_dependency_vulnerability_blocks_release_even_with_other_checks_passing(tmp_path, monkeypatch):
    """Un pip-audit fallido nunca puede reducirse a una advertencia informativa."""
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path / "tests")
    monkeypatch.setattr(gate, "SECURITY_REPORTS", tmp_path / "security")
    monkeypatch.setattr(gate, "FRONTEND_ROOT", tmp_path / "frontend")
    monkeypatch.setattr(gate, "_launcher", lambda _name: None)

    def run(command, *, name, blocking=True, **_kwargs):
        status = gate.FAIL if "pip_audit" in command else gate.PASS
        return gate.GateResult(name, status, blocking=blocking)

    monkeypatch.setattr(gate, "_run", run)
    results = gate.build_gates(SimpleNamespace(with_e2e=False, fast=True))
    monkeypatch.setattr(gate, "build_gates", lambda _args: results)
    output = tmp_path / "result.json"
    assert gate.main(["--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert "auditoria de dependencias (pip-audit)" in report["blocking_failures"]


def test_audit_inventory_excludes_only_private_app_and_keeps_all_installed_third_parties(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.importlib.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": "matrix-rh-backend"}, version="1.3.0"),
        SimpleNamespace(metadata={"Name": "fastapi"}, version="0.141.1"),
        SimpleNamespace(metadata={"Name": "PyYAML"}, version="6.0.2"),
        SimpleNamespace(metadata={"Name": "pip-audit"}, version="2.7.3"),
    ])
    inventory = tmp_path / "requirements.txt"
    gate._write_installed_dependencies(inventory)
    assert set(inventory.read_text().splitlines()) == {"fastapi==0.141.1", "pyyaml==6.0.2", "pip-audit==2.7.3"}


@pytest.mark.parametrize("distributions", [
    [],
    [SimpleNamespace(metadata={"Name": "bad\n-r other.txt"}, version="1")],
    [SimpleNamespace(metadata={"Name": "normal"}, version="1\n--index-url example.test")],
    [SimpleNamespace(metadata={"Name": "normal"}, version="1"),
     SimpleNamespace(metadata={"Name": "normal"}, version="2")],
])
def test_invalid_or_empty_audit_inventory_fails_closed(tmp_path, monkeypatch, distributions):
    monkeypatch.setattr(gate.importlib.metadata, "distributions", lambda: distributions)
    monkeypatch.setattr(gate, "SECURITY_REPORTS", tmp_path)
    result = gate._audit_dependencies("python")
    assert result.status == gate.FAIL
    assert result.blocking


def test_backend_gate_collects_from_tests_root_and_validates_fresh_junit(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)

    def run(command, **kwargs):
        captured.append((command, kwargs))
        return gate.GateResult(kwargs["name"], gate.FAIL)

    monkeypatch.setattr(gate, "_run", run)
    for group in ("standalone", "integration_services"):
        gate._backend_tests("python", group)
    for command, options in captured:
        assert command[command.index("pytest") + 1] == "tests"
        assert "scripts.quality_gate_pytest" in command
        assert options["require_complete_junit"] == Path(command[command.index("--junitxml") + 1])


def test_root_collection_partition_includes_queue_and_dependency_closure(tmp_path):
    """Execute the real plugin against a miniature suite; no production services."""
    tests = tmp_path / "tests"
    (tests / "security").mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    integration: service contract\n")
    (tests / "conftest.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.fixture\ndef require_database():\n    Path('database-called').write_text('yes')\n"
        "@pytest.fixture\ndef indirect_service(require_database):\n    return 'synthetic'\n"
    )
    (tests / "test_chat_queue.py").write_text(
        "from pathlib import Path\ndef test_root_queue():\n    Path('queue-executed').write_text('yes')\n"
    )
    (tests / "security/test_partition.py").write_text(
        "import pytest\n"
        "def test_plain():\n    pass\n"
        "def test_dependency(indirect_service):\n    assert indirect_service == 'synthetic'\n"
        "@pytest.mark.integration\ndef test_marked():\n    pass\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(gate.BACKEND_ROOT)
    environment.pop("PYTEST_ADDOPTS", None)
    inventories = []
    for group in ("standalone", "integration_services"):
        output = tmp_path / (group + ".json")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "-p", "scripts.quality_gate_pytest",
             "--matrix-test-group", group, "--matrix-collection-report", str(output)],
            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        inventories.append(json.loads(output.read_text()))
        if group == "standalone":
            assert (tmp_path / "queue-executed").read_text() == "yes"
            assert not (tmp_path / "database-called").exists()
    assert (tmp_path / "database-called").read_text() == "yes"
    assert inventories[0]["nodeids_sha256"] == inventories[1]["nodeids_sha256"]
    assert all(item["groups"] == {"standalone": 2, "integration_services": 2} for item in inventories)
    assert sum(item["selected"] for item in inventories) == inventories[0]["collected_total"] == 4


def test_zero_exit_with_failed_junit_cannot_pass(tmp_path, monkeypatch):
    report = tmp_path / "junit.xml"

    def run(*_args, **_kwargs):
        report.write_text('<testsuites><testsuite tests="2" failures="1" errors="0"/></testsuites>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate._run(["synthetic"], cwd=tmp_path, name="test", require_complete_junit=report).status == gate.FAIL


def test_caller_pytest_filters_do_not_hide_root_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore=tests/test_chat_queue.py")
    environments = []

    def run(*_args, **kwargs):
        environments.append(kwargs["env"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    gate._run(["python", "-m", "pytest", "tests"], cwd=tmp_path, name="backend")
    assert "PYTEST_ADDOPTS" not in environments[0]


def test_standalone_scope_leaves_real_services_and_models_explicitly_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path / "tests")
    monkeypatch.setattr(gate, "SECURITY_REPORTS", tmp_path / "security")
    monkeypatch.setattr(gate, "FRONTEND_ROOT", tmp_path / "frontend")
    monkeypatch.setattr(gate, "_launcher", lambda _name: None)
    commands = []

    def run(command, *, name, **_kwargs):
        commands.append(command)
        return gate.GateResult(name, gate.PASS)

    monkeypatch.setattr(gate, "_run", run)
    results = gate.build_gates(SimpleNamespace(standalone=True, with_e2e=False, fast=False))
    assert not any("integration_services" in command or "scripts.rag_eval" in command for command in commands)
    for name in ("pruebas backend (integration_services)", "evaluacion RAG (modelos reales, holdout)"):
        result = next(item for item in results if item.name == name)
        assert result.status == gate.SKIPPED and result.blocking


@pytest.mark.parametrize("payload,expected", [
    ({}, gate.INCOMPLETE),
    ({"quality_gate": {"passed": False}, "metrics": {}}, gate.FAIL),
    ({"quality_gate": {"passed": True}, "metrics": {
        "external_coverage_pct": 100, "external_accuracy_measured_cases": 10, "external_accuracy_pct": 100,
        "product_contract_pass_rate_pct": 100, "acl_leakage_count": 0,
    }}, gate.PASS),
    ({"quality_gate": {"passed": True}, "metrics": {
        "external_coverage_pct": 50, "external_accuracy_measured_cases": 10, "external_accuracy_pct": 100,
        "product_contract_pass_rate_pct": 100, "acl_leakage_count": 0,
    }}, gate.INCOMPLETE),
    ({"quality_gate": {"passed": True}, "metrics": {
        "external_coverage_pct": 100, "external_accuracy_measured_cases": 0, "external_accuracy_pct": None,
        "product_contract_pass_rate_pct": 100, "acl_leakage_count": 0,
    }}, gate.INCOMPLETE),
])
def test_full_model_gate_requires_fresh_external_evidence(tmp_path, monkeypatch, payload, expected):
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)

    def run(command, **kwargs):
        assert command[-1] == "--full" and command[command.index("--split") + 1] == "holdout"
        (tmp_path / "rag_eval.json").write_text(json.dumps(payload))
        return gate.GateResult(kwargs["name"], gate.PASS)

    monkeypatch.setattr(gate, "_run", run)
    assert gate._rag_gate("python", fast=False).status == expected


def test_provenance_has_no_dotenv_content_or_arbitrary_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("PRIVATE_TEST_TOKEN", "do-not-record")
    config = tmp_path / "backend/config"
    config.mkdir(parents=True)
    (config / ".env").write_text("DATABASE_URL=private-do-not-record")
    (config / "runtime-manifest.json").write_text('{"version":"synthetic"}')
    report = gate.provenance()
    serialized = json.dumps(report)
    assert "do-not-record" not in serialized and ".env" not in serialized
    assert set(report["configuration_sha256"]) == {"backend/config/runtime-manifest.json"}
    assert report["source"]["commit"] is None


def test_previous_model_evidence_cannot_make_a_new_run_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)
    report = tmp_path / "rag_eval.json"
    report.write_text('{"quality_gate":{"passed":true}}')
    monkeypatch.setattr(gate, "_run", lambda *_args, **kwargs: gate.GateResult(kwargs["name"], gate.PASS))
    assert gate._rag_gate("python", fast=False).status == gate.INCOMPLETE
    assert not report.exists()


def test_backend_success_without_root_collection_evidence_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)
    monkeypatch.setattr(gate, "_run", lambda *_args, **kwargs: gate.GateResult(kwargs["name"], gate.PASS))
    assert gate._backend_tests("python", "standalone").status == gate.FAIL


def test_backend_gate_rejects_tests_removed_after_partition(tmp_path, monkeypatch):
    """A later pytest hook must not make a shortened execution look complete."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tests / "test_cases.py").write_text("def test_first(): pass\ndef test_second(): pass\n")
    (tests / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.hookimpl(trylast=True)\n"
        "def pytest_collection_modifyitems(items):\n"
        "    del items[1:]\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(gate.BACKEND_ROOT))
    monkeypatch.setattr(gate, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)
    result = gate._backend_tests(sys.executable, "standalone")
    inventory = json.loads((tmp_path / "standalone-collection.json").read_text())
    assert inventory["selected"] == 2
    assert result.status == gate.FAIL
    assert "seleccionados" in result.detail


def test_backend_gate_counts_parent_testcases_without_double_counting_subtests(tmp_path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tests / "test_cases.py").write_text(
        "import unittest\n"
        "class Example(unittest.TestCase):\n"
        "    def test_subcases(self):\n"
        "        for value in (1, 2):\n"
        "            with self.subTest(value=value):\n"
        "                self.assertGreater(value, 0)\n"
        "def test_second(): pass\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(gate.BACKEND_ROOT))
    monkeypatch.setattr(gate, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)
    result = gate._backend_tests(sys.executable, "standalone")
    assert result.status == gate.PASS
    assert result.junit_testcases == result.collection["selected"] == 2


@pytest.mark.parametrize("external,expected", [(True, gate.PASS), (False, gate.FAIL), (None, gate.FAIL)])
def test_gate_consumes_the_actual_evaluation_report_contract(tmp_path, monkeypatch, external, expected):
    """Cross-script shape check only; no inference or claim of measured model quality."""
    from scripts.rag_eval import CaseResult, EvaluationReport

    report = EvaluationReport(mode="full", metadata={"selection_verified": True}, results=[
        CaseResult("synthetic-case", "synthetic-user", "grounded", passed=True,
                   external_accuracy_pass=external, evaluation_group="synthetic-group", evaluation_split="holdout"),
    ])
    monkeypatch.setattr(gate, "TEST_REPORTS", tmp_path)

    def run(_command, **kwargs):
        (tmp_path / "rag_eval.json").write_text(json.dumps(report.as_dict()))
        return gate.GateResult(kwargs["name"], gate.PASS)

    monkeypatch.setattr(gate, "_run", run)
    assert gate._rag_gate("python", fast=False).status == expected
