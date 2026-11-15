# Creado por Aldo Garcia.
"""Inventario CycloneDX 1.6 reproducible de ambos locks, sin red ni instalacion.

Incluye dependencias opcionales, de desarrollo y de otras plataformas. No es un
inventario del equipo desplegado: no incluye SO, modelos ni herramientas externas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _component(ecosystem: str, name: str, version: str) -> dict:
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        raise ValueError("Cada paquete debe declarar nombre y version.")
    if ecosystem == "pypi":
        name = re.sub(r"[-_.]+", "-", name).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError("Nombre PyPI no valido.")
        namespace, component_name = "", name
    else:
        if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
            raise ValueError("Nombre npm no valido.")
        namespace, _, component_name = name.rpartition("/")
    purl_name = f"{quote(namespace, safe='')}/" if namespace else ""
    purl_name += quote(component_name, safe="")
    purl = f"pkg:{ecosystem}/{purl_name}@{quote(version, safe='')}"
    result = {"type": "library", "bom-ref": purl, "name": component_name, "version": version, "purl": purl}
    if namespace:
        result["group"] = namespace
    return result


def build_sbom(root: Path) -> dict:
    uv_bytes = (root / "backend/uv.lock").read_bytes()
    npm_bytes = (root / "frontend/package-lock.json").read_bytes()
    uv_lock = tomllib.loads(uv_bytes.decode("utf-8"))
    npm_lock = json.loads(npm_bytes)
    if uv_lock.get("version") != 1 or not isinstance(uv_lock.get("package"), list):
        raise ValueError("Formato uv.lock no soportado.")
    if npm_lock.get("lockfileVersion") != 3 or not isinstance(npm_lock.get("packages"), dict):
        raise ValueError("Se requiere package-lock.json version 3.")
    components = {}
    for package in uv_lock["package"]:
        source = package.get("source", {})
        if source == {"virtual": "."} or source == {"editable": "."}:
            continue  # El proyecto es metadata; no es una dependencia de registro.
        if set(source) != {"registry"}:
            raise ValueError("Dependencia uv sin registro: ampliar el inventario antes de publicar.")
        component = _component("pypi", package.get("name"), package.get("version"))
        components[component["purl"]] = component
    for location, package in npm_lock["packages"].items():
        if not location:
            continue
        if "node_modules/" not in location or package.get("link"):
            raise ValueError("Dependencia npm local/enlazada no soportada.")
        name = package.get("name", location.rsplit("node_modules/", 1)[1])
        component = _component("npm", name, package.get("version"))
        components[component["purl"]] = component
    project = npm_lock["packages"].get("", {})
    project_version = project.get("version")
    if not isinstance(project_version, str) or not project_version:
        raise ValueError("El lock frontend no declara version del proyecto.")
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "lifecycles": [{"phase": "pre-build"}],
            "component": {"type": "application", "name": "Matrix RH", "version": project_version},
            "properties": [
                {
                    "name": "matrix:inventory:scope",
                    "value": (
                        "All locked registry dependencies, including dev, optional and platform variants; "
                        "not installed runtime, OS or models. Dependency edges and licenses are not inferred."
                    ),
                },
                {"name": "matrix:lock:backend/uv.lock:sha256", "value": hashlib.sha256(uv_bytes).hexdigest()},
                {
                    "name": "matrix:lock:frontend/package-lock.json:sha256",
                    "value": hashlib.sha256(npm_bytes).hexdigest(),
                },
            ],
        },
        "components": [components[key] for key in sorted(components)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "backend/var/reports/sbom.cdx.json")
    args = parser.parse_args(argv)
    try:
        bom = build_sbom(PROJECT_ROOT)
        output = json.dumps(bom, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"No se genero el SBOM: {exc}\n")
    print(f"SBOM: {len(bom['components'])} componentes de locks; {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
