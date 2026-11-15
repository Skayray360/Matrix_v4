# Creado por Aldo Garcia.
"""Inventario de vectores heredados; no borra hasta proporcionar --apply.

Ejecutar en mantenimiento con la configuracion SQL/Qdrant correcta. En embedded,
detener Matrix antes de abrir el indice. Revisar primero el inventario y repetir
el mismo scope/offset con --apply --expected-manifest-count N. Una BD vacia exige
ademas --allow-empty-manifest. Continuar con next_offset hasta terminar la pasada.
No recupera texto ni embeddings, no modifica manifests ni elimina generaciones activas.
"""

from __future__ import annotations

import argparse
import json
from uuid import UUID


def _offset(value: str) -> str | int:
    if value.isdecimal():
        number = int(value)
        if 0 <= number < 2 ** 64:
            return number
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("El offset debe ser un UUID o entero valido de Qdrant.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("corporate", "conversation"), required=True)
    parser.add_argument("--limit", type=int, default=128)
    parser.add_argument("--offset", type=_offset)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-manifest-count", type=int)
    parser.add_argument("--allow-empty-manifest", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 128:
        parser.error("--limit debe estar entre 1 y 128")
    if args.apply and (args.expected_manifest_count is None or args.expected_manifest_count < 0):
        parser.error("--apply requiere --expected-manifest-count con el conteo del inventario previo")

    from sqlalchemy.exc import SQLAlchemyError

    from app.common.errors import DatabaseUnavailableError, QdrantUnavailableError
    from app.database.engine import session_scope
    from app.ingestion.legacy_vector_cleanup import review_legacy_generations
    from app.rag.vector_store import get_vector_store, reset_vector_store

    try:
        with session_scope() as db:
            report = review_legacy_generations(
                db, store=get_vector_store(), scope=args.scope, limit=args.limit, offset=args.offset,
                apply=args.apply, expected_manifest_count=args.expected_manifest_count,
                allow_empty_manifest=args.allow_empty_manifest,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["invalid_metadata"] else 0
    except (ValueError, SQLAlchemyError, DatabaseUnavailableError, QdrantUnavailableError) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "message": "El lote no se completo; revise configuracion, bloqueos y conteo SQL. "
                                     "Reintentar el mismo offset es seguro; no se infiere ausencia de un error."}))
        return 1
    finally:
        reset_vector_store()


if __name__ == "__main__":
    raise SystemExit(main())
