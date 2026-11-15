# Creado por Aldo Garcia.
"""Prueba Windows de la DACL real del exportador, sin importar app ni dependencias.

Usa solo archivos sinteticos temporales. Extrae la funcion del codigo de
produccion para no validar una copia independiente de la implementacion.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path


class AclVerificationError(RuntimeError):
    """Diagnostico exclusivamente de operaciones sobre fixtures temporales."""

    def __init__(self, *, stage: str, returncode: int, stderr: str):
        super().__init__("Fallo de ACL sobre fixture sintetico.")
        self.report = {"status": "failed", "error_type": "CalledProcessError", "stage": stage,
                       "returncode": returncode, "synthetic_stderr": stderr[:6000]}


class SyntheticSubprocess:
    """Captura errores de la funcion real sin cambiar su privacidad en produccion."""

    DEVNULL = subprocess.DEVNULL

    def __init__(self, paths: set[Path]):
        self.paths = {str(path) for path in paths}
        self.stage = "unassigned"

    def run(self, command, **kwargs):
        environment = kwargs.get("env", {})
        observed = {value for key, value in environment.items() if key in {
            "MATRIX_REVIEW_FILE", "MATRIX_ACL_FILE", "MATRIX_ACL_DIRECTORY", "MATRIX_ACL_CHILD",
        }}
        if not observed or not observed.issubset(self.paths):
            raise ValueError("El diagnostico solo admite rutas temporales sinteticas.")
        kwargs.pop("stdout", None)
        kwargs.pop("stderr", None)
        kwargs.update(capture_output=True, text=True, check=False)
        result = subprocess.run(command, **kwargs)  # noqa: S603 -- comandos constantes del harness/funcion real.
        if result.returncode:
            raise AclVerificationError(stage=self.stage, returncode=result.returncode, stderr=result.stderr)
        return result


def load_acl_function(subprocess_module=subprocess):
    source = Path(__file__).with_name("rag_chunk_export.py")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_windows_private_file"]
    if len(functions) != 1:
        raise ValueError("No se encontro la funcion de DACL del exportador.")
    namespace = {"Path": Path, "os": os, "subprocess": subprocess_module}
    code = compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec")
    exec(code, namespace)  # noqa: S102 -- funcion exacta del repositorio, sin entrada externa.
    return namespace["_windows_private_file"]


def verify() -> dict:
    powershell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    with tempfile.TemporaryDirectory(prefix="matrix-acl-test-") as directory:
        root = Path(directory)
        protected_file = root / "private '$() file.json"
        protected_directory = root / "private '$() directory"
        child = protected_directory / "descendant.json"
        diagnostic = SyntheticSubprocess({protected_file, protected_directory, child})
        private = load_acl_function(diagnostic)
        protected_file.write_text("synthetic", encoding="utf-8")
        diagnostic.stage = "protect_file"
        private(protected_file)
        protected_directory.mkdir()
        diagnostic.stage = "protect_directory"
        private(protected_directory)
        child.write_text("synthetic", encoding="utf-8")
        script = (
            "$ErrorActionPreference='Stop';"
            "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;"
            "$items=@(@($env:MATRIX_ACL_FILE,$true,'file'),@($env:MATRIX_ACL_DIRECTORY,$true,'directory'),"
            "@($env:MATRIX_ACL_CHILD,$false,'inherited_child'));"
            "foreach($item in $items){$acl=Get-Acl -LiteralPath $item[0];"
            "if($acl.AreAccessRulesProtected -ne $item[1]){throw ($item[2]+': protection mismatch')};"
            "$rules=$acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]);"
            "if($rules.Count -ne 1){throw ($item[2]+': unexpected rule count '+$rules.Count)};"
            "foreach($r in $rules){if($r.IdentityReference -ne $sid -or $r.AccessControlType -ne 'Allow'"
            " -or $r.FileSystemRights -ne 'FullControl'){throw ($item[2]+': unexpected grant')}}};"
            "Write-Output 'PRIVATE_ACL_VERIFIED'"
        )
        diagnostic.stage = "verify_file_directory_inherited_child"
        result = diagnostic.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "MATRIX_ACL_FILE": str(protected_file),
                 "MATRIX_ACL_DIRECTORY": str(protected_directory), "MATRIX_ACL_CHILD": str(child)},
            check=True, timeout=15, capture_output=True, text=True,
        )
        if result.stdout.strip() != "PRIVATE_ACL_VERIFIED":
            raise ValueError("No se verificaron las ACL privadas.")
    return {"status": "passed", "platform": "windows", "checked": ["file", "directory", "inherited_child"],
            "fixtures": "synthetic_temporary_only", "application_dependencies_imported": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-windows", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print(json.dumps({"status": "skipped", "reason": "Windows required for actual DACL validation"}))
        return 1 if args.require_windows else 0
    try:
        report = verify()
    except AclVerificationError as error:
        print(json.dumps(error.report))
        return 1
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
