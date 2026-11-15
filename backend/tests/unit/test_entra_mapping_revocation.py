# Creado por Aldo Garcia.
"""Prune retira el permiso anterior y obliga a renovar claims de Entra."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.common.ids import utcnow_naive
from app.database.models import Base, EntraGroupRoleMapping, Role, SessionRecord, User, UserRole
from app.structured_data.sources import SourceCatalog
from scripts import load_entra_mapping as mapping

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture
def mapping_database(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = utcnow_naive()
    with Session(engine) as db:
        db.add_all([Role(id=name, name=name, is_test_role=False) for name in ("admin", "reader", "manual")])
        for user_id, provider in (("federated", "entra"), ("local", "local_password")):
            db.add(User(id=user_id, username=user_id, display_name=user_id, auth_source=provider))
            db.add_all([UserRole(user_id=user_id, role_id=role) for role in ("admin", "manual")])
            db.add(SessionRecord(id=f"session-{user_id}", session_token_hash=user_id, user_id=user_id,
                                 csrf_token="synthetic-csrf", auth_source=provider,
                                 expires_at=now + timedelta(hours=1)))
        db.add(EntraGroupRoleMapping(id="old", provider="entra", external_kind="group",
                                    external_key="group-a", role_id="admin"))
        db.commit()

    @contextmanager
    def session_scope():
        with Session(engine) as db:
            yield db
            db.commit()

    selected = [mapping.MappingFile(mappings=[mapping.MappingEntry(external_key="group-a", role="reader")])]
    monkeypatch.setattr(mapping, "session_scope", session_scope)
    monkeypatch.setattr(mapping, "load_mapping_file", lambda: selected[0])
    monkeypatch.setattr(mapping, "load_sources", lambda: SourceCatalog(sources={}))
    yield engine, selected
    engine.dispose()


def assert_revocation_state(engine, *, revoked):
    with Session(engine) as db:
        federated_roles = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == "federated")))
        local_roles = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == "local")))
        assert federated_roles == ({"manual"} if revoked else {"admin", "manual"})
        assert local_roles == {"admin", "manual"}
        assert (db.get(SessionRecord, "session-federated").revoked_at is not None) is revoked
        assert db.get(SessionRecord, "session-local").revoked_at is None


def test_reassigning_external_group_prunes_old_role_and_revokes_only_provider_users(mapping_database):
    engine, _ = mapping_database
    stats = mapping.sync(prune=True)
    assert stats["created"] == stats["pruned"] == stats["roles_revoked"] == stats["sessions_revoked"] == 1
    with Session(engine) as db:
        assert list(db.scalars(select(EntraGroupRoleMapping.role_id))) == ["reader"]
    assert_revocation_state(engine, revoked=True)
    repeated = mapping.sync(prune=True)
    assert all(repeated[key] == 0 for key in ("created", "pruned", "roles_revoked", "sessions_revoked"))


def test_removing_last_mapping_does_not_leave_ungoverned_role_assignment(mapping_database):
    engine, selected = mapping_database
    selected[0] = mapping.MappingFile()
    stats = mapping.sync(prune=True)
    assert stats["roles_revoked"] == 1
    assert_revocation_state(engine, revoked=True)


def test_role_still_mapped_elsewhere_requires_new_claims_but_is_not_removed_globally(mapping_database):
    engine, selected = mapping_database
    with Session(engine) as db:
        db.add(EntraGroupRoleMapping(id="retained", provider="entra", external_kind="group",
                                    external_key="group-b", role_id="admin"))
        db.commit()
    selected[0].mappings.append(mapping.MappingEntry(external_key="group-b", role="admin"))
    stats = mapping.sync(prune=True)
    assert stats["pruned"] == stats["sessions_revoked"] == 1
    assert stats["roles_revoked"] == 0
    with Session(engine) as db:
        assert db.get(UserRole, ("federated", "admin")) is not None
        assert db.get(SessionRecord, "session-federated").revoked_at is not None


def test_dry_run_reports_reassignment_without_persisting_roles_or_revocations(mapping_database):
    engine, _ = mapping_database
    stats = mapping.sync(prune=True, dry_run=True)
    assert stats["created"] == stats["pruned"] == 1
    assert stats["roles_revoked"] == stats["sessions_revoked"] == 0
    with Session(engine) as db:
        assert list(db.scalars(select(EntraGroupRoleMapping.role_id))) == ["admin"]
    assert_revocation_state(engine, revoked=False)


def test_without_prune_sync_remains_additive(mapping_database):
    engine, _ = mapping_database
    stats = mapping.sync()
    assert stats["created"] == 1
    assert stats["pruned"] == 0
    assert_revocation_state(engine, revoked=False)
