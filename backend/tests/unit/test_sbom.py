# Creado por Aldo Garcia.
"""El inventario debe reflejar locks completos, ser estable y no filtrar URLs."""

import json
import tomllib
from pathlib import Path

import pytest
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonValidator

from scripts.generate_sbom import PROJECT_ROOT, build_sbom


def _locks(tmp_path: Path, *, package: dict | None = None, uv_source: str = 'registry = "https://example.invalid"'):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend/uv.lock").write_text(
        'version = 1\n[[package]]\nname = "Fake_Package"\nversion = "1.0.0"\n'
        f"source = {{ {uv_source} }}\n", encoding="utf-8",
    )
    dependency = package if package is not None else {
        "version": "2.0.0", "dev": True, "optional": True,
        "resolved": "https://user:credential@example.invalid/private/package.tgz",  # secrets-scan: allow (fixture sintetico para comprobar que el SBOM no exporta credenciales de registro)
    }
    (tmp_path / "frontend/package-lock.json").write_text(json.dumps({
        "lockfileVersion": 3,
        "packages": {"": {"version": "1.4.2"}, "node_modules/@scope/pkg": dependency},
    }), encoding="utf-8")


def test_complete_repository_inventory_validates_against_cyclonedx_schema():
    bom = build_sbom(PROJECT_ROOT)
    assert JsonValidator(SchemaVersion.V1_6).validate_str(json.dumps(bom)) is None
    uv = tomllib.loads((PROJECT_ROOT / "backend/uv.lock").read_text())
    npm = json.loads((PROJECT_ROOT / "frontend/package-lock.json").read_text())
    expected_python = {(p["name"], p["version"]) for p in uv["package"] if "registry" in p["source"]}
    actual_python = {(p["name"], p["version"]) for p in bom["components"] if p["purl"].startswith("pkg:pypi/")}
    assert actual_python == expected_python
    expected_npm = {(p.get("name", path.rsplit("node_modules/", 1)[-1]), p["version"])
                    for path, p in npm["packages"].items() if path}
    actual_npm = {(f"{p['group']}/{p['name']}" if "group" in p else p["name"], p["version"])
                  for p in bom["components"] if p["purl"].startswith("pkg:npm/")}
    assert actual_npm == expected_npm


def test_reproducible_scoped_purls_optional_dependencies_and_no_credentials(tmp_path):
    _locks(tmp_path)
    bom = build_sbom(tmp_path)
    assert bom == build_sbom(tmp_path)
    assert {p["purl"] for p in bom["components"]} == {"pkg:pypi/fake-package@1.0.0", "pkg:npm/%40scope/pkg@2.0.0"}
    assert "credential" not in json.dumps(bom)
    assert "example.invalid" not in json.dumps(bom)
    assert "timestamp" not in bom["metadata"]
    assert bom["metadata"]["lifecycles"] == [{"phase": "pre-build"}]


@pytest.mark.parametrize("package", [{"link": True}, {}, {"version": None}])
def test_unrepresentable_dependency_is_not_silently_omitted(tmp_path, package):
    _locks(tmp_path, package=package)
    with pytest.raises(ValueError):
        build_sbom(tmp_path)


def test_rejects_unknown_source_and_lock_format(tmp_path):
    _locks(tmp_path, uv_source='git = "https://example.invalid/repo"')
    with pytest.raises(ValueError, match="sin registro"):
        build_sbom(tmp_path)


def test_nested_duplicate_is_deduplicated_without_losing_other_versions(tmp_path):
    _locks(tmp_path)
    path = tmp_path / "frontend/package-lock.json"
    npm = json.loads(path.read_text())
    npm["packages"]["node_modules/parent/node_modules/@scope/pkg"] = {"version": "2.0.0"}
    npm["packages"]["node_modules/other/node_modules/@scope/pkg"] = {"version": "1.0.0"}
    path.write_text(json.dumps(npm))
    bom = build_sbom(tmp_path)
    assert len(bom["components"]) == 3
    old_hash = bom["metadata"]["properties"][-1]["value"]
    path.write_text(json.dumps(npm, indent=2))
    assert build_sbom(tmp_path)["metadata"]["properties"][-1]["value"] != old_hash
