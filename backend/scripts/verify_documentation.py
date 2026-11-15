# Creado por Aldo Garcia.
"""Verifica destinos de enlaces locales en la documentacion distribuible.

No realiza peticiones de red ni ejecuta comandos incluidos en las guias.
Los fragmentos #ancla no se validan; se comprueba el archivo de destino.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
_FENCES = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_LINKS = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[\"'][^\n]*?[\"'])?\s*\)")
_DATA_PREFIXES = (
    "knowledge-base/", "backend/tests/fixtures/", "backend/runtime/",
    "backend/logs/", "backend/config/secrets/", "data/",
)


def _project_markdown(root: Path, source: Path, *, workspace: bool = False) -> bool:
    relative = source.relative_to(root).as_posix()
    return (
        source.suffix.lower() == ".md"
        and not relative.startswith(_DATA_PREFIXES)
        and not (workspace and relative.startswith("reports/"))
    )


def check_readme_layout(root: Path, files: list[Path]) -> list[str]:
    """La entrega tiene una guia operativa unica, sin reatribuir documentos."""
    readmes = [
        path.relative_to(root).as_posix() for path in files
        if _project_markdown(root, path) and path.name.casefold() == "readme.md"
    ]
    errors = [] if "README.md" in readmes else ["Falta README.md en la raiz del paquete."]
    errors.extend(f"README adicional fuera de la guia unica: {path}" for path in readmes if path != "README.md")
    return errors


def check_links(root: Path, files: list[Path], *, workspace: bool = False) -> list[str]:
    """Comprueba enlaces Markdown simples contra el contenido seleccionado."""
    root = root.resolve()
    selected = {path.resolve() for path in files}
    errors: list[str] = []
    for source in files:
        if not _project_markdown(root, source, workspace=workspace):
            continue
        relative = source.relative_to(root).as_posix()
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            errors.append(f"{relative}: la guia debe ser un archivo local regular.")
            continue
        content = _FENCES.sub("", source.read_text(encoding="utf-8-sig"))
        for match in _LINKS.finditer(content):
            raw = match.group(1) or match.group(2)
            parsed = urlsplit(raw)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (source.parent / unquote(parsed.path)).resolve()
            if not target.is_relative_to(root):
                errors.append(f"{relative}: enlace fuera del paquete: {raw}")
            elif workspace and target.is_relative_to(root / "reports") and target.is_file():
                # Un informe historico referenciado puede existir en Git sin
                # pertenecer al paquete distribuible (p. ej. JSON de pruebas).
                continue
            elif target not in selected and not (
                target.is_dir() and any(path.is_relative_to(target) for path in selected)
            ):
                errors.append(f"{relative}: destino no distribuido: {raw}")
    return errors


def check_release_versions(root: Path) -> list[str]:
    """Contrasta metadata activa sin importar la aplicacion ni reescribir historia.

    El manifiesto de modelos describe la configuracion de esta entrega; alinear
    su release no convierte el inventario declarado en evidencia del runtime.
    Informes de auditoria anteriores conservan sus versiones y recuentos.
    """
    try:
        project = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))["project"]
        expected = project["version"]
        if not isinstance(expected, str) or not re.fullmatch(r"\d+\.\d+\.\d+", expected):
            raise ValueError("VersionInvalida")
        tree = ast.parse((root / "backend/app/__init__.py").read_text(encoding="utf-8-sig"))
        declarations = [
            node.value.value for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
        ]
        backend_lock = tomllib.loads((root / "backend/uv.lock").read_text(encoding="utf-8"))
        locked_project = [package.get("version") for package in backend_lock["package"]
                          if package.get("name") == project["name"]]
        frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
        frontend_lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
        models = json.loads((root / "backend/config/model-manifest.json").read_text(encoding="utf-8"))
        checks = {
            "backend/app/__init__.py": declarations == [expected],
            "backend/uv.lock": locked_project == [expected],
            "frontend/package.json": frontend["version"] == expected,
            "frontend/package-lock.json": (
                frontend_lock["version"] == expected and frontend_lock["packages"][""]["version"] == expected
            ),
            "backend/config/model-manifest.json": models["release"] == expected,
        }
        errors = [f"{path}: version activa no coincide con pyproject.toml ({expected})."
                  for path, matches in checks.items() if not matches]
        runtime = json.loads((root / "backend/config/runtime-manifest.json").read_text(encoding="utf-8"))
        dockerfile = (root / "backend/Dockerfile").read_text(encoding="utf-8")
        for tool in ("node", "python"):
            versions = re.findall(rf"(?m)^FROM {tool}:(\d+\.\d+\.\d+)[^\s]*", dockerfile)
            if versions != [runtime[tool]["version"]]:
                errors.append(f"backend/Dockerfile: {tool} no coincide con runtime-manifest.json.")
        return errors
    except (OSError, SyntaxError, ValueError, KeyError, TypeError) as exc:
        # Los errores se limitan al tipo: no volcar contenido de archivos ajenos.
        return [f"Metadata de version no verificable ({type(exc).__name__})."]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--workspace", action="store_true",
        help="valida guias activas del checkout y sus destinos; no audita el interior de snapshots reports",
    )
    args = parser.parse_args(argv)
    from scripts.package_release import iter_package_files

    root = args.root.resolve()
    files = iter_package_files(root)
    errors = (check_readme_layout(root, files) + check_links(root, files, workspace=args.workspace)
              + check_release_versions(root))
    markdown_files = sum(
        _project_markdown(root, p, workspace=args.workspace) for p in files
    )
    print(json.dumps({"ok": not errors, "scope": "active_workspace" if args.workspace else "distributed_package",
                      "markdown_files": markdown_files,
                      "errors": errors}, indent=2, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
