# Creado por Aldo Garcia.
"""Las bases sinteticas deben rechazar las mismas duplicidades que MySQL."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import (
    Base,
    CategoryPermission,
    EntraGroupRoleMapping,
    IdentityLink,
    StructuredSourcePermission,
    StructuredSourcePolicy,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("model,values", [
    (IdentityLink, {"user_id": "user", "provider": "entra", "subject_id": "subject"}),
    (CategoryPermission, {"role_id": "role", "category": "prestaciones", "access": "read"}),
    (EntraGroupRoleMapping, {"role_id": "role", "provider": "entra", "external_kind": "group", "external_key": "group"}),
    (StructuredSourcePermission, {"role_id": "role", "source_name": "rh_demo"}),
    (StructuredSourcePolicy, {"source_name": "rh_demo", "entity": "plantilla", "allowed_columns": ["id"]}),
])
def test_synthetic_database_rejects_duplicate_identity_and_authorization_rows(model, values):
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([model(**values), model(**values)])
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
    finally:
        engine.dispose()


def test_orm_preserves_every_unique_key_declared_in_initial_migration():
    migration = Path(__file__).resolve().parents[2] / "migrations" / "0001_initial_schema.sql"
    ddl = migration.read_text(encoding="utf-8")
    for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\) ENGINE=", ddl, re.S):
        declared = {
            tuple(part.strip() for part in columns.split(","))
            for columns in re.findall(r"UNIQUE KEY \w+\s*\(([^)]+)\)", body)
        }
        model_keys = {
            tuple(column.name for column in constraint.columns)
            for constraint in Base.metadata.tables[table].constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert declared <= model_keys, f"{table}: claves unicas ausentes del ORM: {declared - model_keys}"
