# Creado por Aldo Garcia.
"""Una politica rota no debe emitir una aprobacion de cadena de suministro."""

from __future__ import annotations

import pytest
import yaml

from app.common.errors import ConfigurationError
from scripts import verify_supply_chain as vsc

pytestmark = [pytest.mark.security, pytest.mark.unit]


@pytest.fixture()
def policy():
    return {
        "version": 1,
        "incidents": [{"id": "synthetic", "packages": [{"name": "blocked", "versions": ["1.2.3"]}]}],
        "indicators": {"filenames": ["synthetic-bad*"], "content_patterns": [r"synthetic\.invalid"]},
    }


@pytest.mark.parametrize("payload", ["", "{}", "null", "[]", "true", "1", "invalid: ["])
@pytest.mark.parametrize("lock_only", [False, True])
def test_empty_or_malformed_policy_fails_closed(tmp_path, monkeypatch, payload, lock_only):
    path = tmp_path / "policy.yaml"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(vsc, "DENYLIST_PATH", path)
    monkeypatch.setattr(vsc, "check_reproducible_install", lambda _: pytest.fail("no debe aprobar sin politica"))

    with pytest.raises(ConfigurationError):
        vsc.load_denylist()
    report = vsc.run(lock_only=lock_only)

    assert not report.ok
    assert [item.kind for item in report.blocking] == ["politica_de_suministro_invalida"]
    assert not any(result.startswith("OK") for result in report.checks.values())


@pytest.mark.parametrize(("path", "value"), [
    (("version",), True),
    (("version",), "1"),
    (("version",), 2),
    (("incidents",), []),
    (("incidents",), {}),
    (("incidents", 0), None),
    (("incidents", 0, "id"), ""),
    (("incidents", 0, "packages"), []),
    (("incidents", 0, "packages"), {}),
    (("incidents", 0, "packages", 0), "blocked"),
    (("incidents", 0, "packages", 0, "name"), ""),
    (("incidents", 0, "packages", 0, "name"), "blocked "),
    (("incidents", 0, "packages", 0, "versions"), []),
    (("incidents", 0, "packages", 0, "versions"), ["*"]),
    (("incidents", 0, "packages", 0, "versions"), None),
    (("incidents", 0, "packages", 0, "versions"), 1.2),
    (("incidents", 0, "packages", 0, "versions"), [1.2]),
    (("incidents", 0, "packages", 0, "versions"), [""]),
    (("indicators",), {}),
    (("indicators",), []),
    (("indicators", "filenames"), []),
    (("indicators", "filenames"), "synthetic-bad*"),
    (("indicators", "content_patterns"), []),
    (("indicators", "content_patterns"), [None]),
    (("indicators", "content_patterns"), ["["]),
])
def test_invalid_nested_rule_is_rejected(tmp_path, policy, path, value):
    parent = policy
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    target = tmp_path / "policy.yaml"
    target.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(ConfigurationError):
        vsc.load_denylist(target)


@pytest.mark.parametrize("key", ["version", "incidents", "indicators"])
def test_missing_required_policy_section_is_rejected(tmp_path, policy, key):
    del policy[key]
    target = tmp_path / "policy.yaml"
    target.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(ConfigurationError):
        vsc.load_denylist(target)


@pytest.mark.parametrize("versions", ["*", "1.2.3", ["1.2.3", "2.0.0"]])
def test_valid_policy_retains_blocking_semantics(tmp_path, policy, versions):
    policy["incidents"][0]["packages"][0]["versions"] = versions
    target = tmp_path / "policy.yaml"
    target.write_text(yaml.safe_dump(policy), encoding="utf-8")
    loaded = vsc.load_denylist(target)
    report = vsc.Report()

    vsc.check_denylist(report, loaded, [("blocked", "1.2.3", tmp_path / "blocked", {})])

    assert not report.ok
    assert any(item.kind == "paquete_en_lista_de_bloqueo" for item in report.blocking)
