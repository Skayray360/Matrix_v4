# Creado por Aldo Garcia.
"""Compara el build recien generado con el dist del commit, incluyendo extras."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = "frontend/dist"


def git(root: Path, *args: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise FileNotFoundError("GitRequired")
    return subprocess.run(  # noqa: S603 - CLI de CI, argumentos internos fijos, sin shell
        [executable, "-C", str(root), *args], check=True, capture_output=True,
    ).stdout


def compare_dist(root: Path, expected: dict[str, bytes]) -> list[str]:
    """Compara inventario y bytes; tambien detecta archivos ignorados por Git."""
    if not expected:
        return ["El commit no contiene frontend/dist; no se puede certificar el build."]
    dist = root / DIST
    if dist.is_symlink() or not dist.is_dir():
        return ["frontend/dist debe existir como directorio local regular."]
    errors: list[str] = []
    actual = {}
    for path in dist.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(f"{relative}: dist no admite enlaces simbolicos.")
        elif path.is_file():
            actual[relative] = path
    errors.extend(f"{path}: archivo no generado." for path in sorted(expected.keys() - actual.keys()))
    errors.extend(f"{path}: archivo extra en el build." for path in sorted(actual.keys() - expected.keys()))
    for path in sorted(expected.keys() & actual.keys()):
        if actual[path].read_bytes() != expected[path]:
            errors.append(f"{path}: bytes diferentes al commit; reconstruya y revise dist.")
    return errors


def check_dist(root: Path) -> list[str]:
    """No modifica Git ni el build; no depende de .gitignore o del indice staged."""
    try:
        expected = {}
        for record in git(root, "ls-tree", "-r", "-z", "HEAD", "--", DIST).split(b"\0"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            path = raw_path.decode("utf-8")
            if mode not in {b"100644", b"100755"} or kind != b"blob":
                return [f"{path}: dist solo admite archivos regulares."]
            expected[path] = git(root, "cat-file", "blob", oid.decode("ascii"))
        return compare_dist(root, expected)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return [f"Build no verificable ({type(exc).__name__})."]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    errors = check_dist(parser.parse_args().root.resolve())
    print(json.dumps({"ok": not errors, "reference": "HEAD:frontend/dist", "errors": errors}, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
