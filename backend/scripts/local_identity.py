# Creado por Aldo Garcia.
"""Bootstrap y mantenimiento local de identidad; nunca imprime contrasenas."""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.audit.service import AuditRecord, get_audit_service
from app.auth.local_accounts import (
    LOCAL_ADMIN_ROLE,
    LOCAL_READER_ROLE,
    bootstrap_administrator,
    create_local_account,
    replace_local_password,
    update_local_account,
    validate_password,
)
from app.common.errors import MatrixError, ValidationFailedError
from app.config import PROJECT_ROOT
from app.database.engine import session_scope
from app.database.models import User


def read_password(path: Path | None, *, root: Path = PROJECT_ROOT) -> str:
    if path is None:
        password = getpass.getpass("Nueva contrasena: ")
        confirmation = getpass.getpass("Repita la contrasena: ")
        if password != confirmation:
            raise ValidationFailedError("Las contrasenas no coinciden.")
    else:
        secret_root = (root / "backend/config/secrets").resolve()
        target = path if path.is_absolute() else root / path
        if (target.is_symlink() or target.absolute() != target.resolve()
                or not target.resolve().is_relative_to(secret_root) or not target.is_file()
                or target.stat().st_size > 1024):
            raise ValidationFailedError("Use un archivo regular dentro de backend/config/secrets.")
        password = target.read_text(encoding="utf-8-sig").rstrip("\r\n")
    validate_password(password)
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("bootstrap", "create-user", "change-password"):
        command = commands.add_parser(name)
        command.add_argument("--username", required=True)
        command.add_argument("--password-file", type=Path, required=name == "bootstrap")
        if name == "create-user":
            command.add_argument("--display-name", default="")
            command.add_argument("--role", choices=(LOCAL_ADMIN_ROLE, LOCAL_READER_ROLE), default=LOCAL_READER_ROLE)
    for name in ("set-active", "set-role"):
        command = commands.add_parser(name)
        command.add_argument("--username", required=True)
        if name == "set-role":
            command.add_argument("--role", choices=(LOCAL_ADMIN_ROLE, LOCAL_READER_ROLE), required=True)
        else:
            state = command.add_mutually_exclusive_group(required=True)
            state.add_argument("--active", dest="is_active", action="store_true")
            state.add_argument("--inactive", dest="is_active", action="store_false")
    args = parser.parse_args(argv)
    try:
        password = read_password(args.password_file) if hasattr(args, "password_file") else None
        with session_scope() as db:
            if args.action == "bootstrap":
                _, created = bootstrap_administrator(db, username=args.username, password=password)
                result = {"ok": True, "action": args.action, "created": created, "credentials_reset": False}
            elif args.action == "create-user":
                create_local_account(db, username=args.username, display_name=args.display_name or args.username,
                                     password=password, role_name=args.role)
                result = {"ok": True, "action": args.action, "created": True}
            elif args.action == "change-password":
                user = db.execute(select(User).where(User.username == args.username)
                                  .with_for_update()).scalar_one_or_none()
                if user is None:
                    raise ValidationFailedError("No existe la cuenta local solicitada.")
                count = replace_local_password(db, user=user, password=password)
                result = {"ok": True, "action": args.action, "revoked_sessions": count}
            else:
                # Solo localizar el ID; update_local_account toma los locks en
                # orden rol -> usuarios -> sesiones, tambien desde el CLI.
                user_id = db.execute(select(User.id).where(User.username == args.username)).scalar_one_or_none()
                if user_id is None:
                    raise ValidationFailedError("No existe la cuenta local solicitada.")
                user, count = update_local_account(
                    db, user_id=user_id,
                    is_active=args.is_active if args.action == "set-active" else None,
                    role_name=args.role if args.action == "set-role" else None,
                )
                get_audit_service().record(db, AuditRecord(
                    request_id="local-operator", event_type="auth.local_user_updated_cli",
                    resource=user.id, authorization_decision="ALLOW",
                ))
                result = {"ok": True, "action": args.action, "is_active": user.is_active,
                          "revoked_sessions": count}
        print(json.dumps(result))
        return 0
    except (MatrixError, SQLAlchemyError, OSError, UnicodeError, ValueError):
        print(json.dumps({"ok": False, "action": args.action,
                          "message": "Revise el proveedor local, la cuenta, la base y el archivo privado; "
                                     "no se modificaron credenciales existentes por bootstrap."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
