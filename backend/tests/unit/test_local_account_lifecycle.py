# Creado por Aldo Garcia.
"""Bajas y cambios locales con SQLite temporal; sin usuarios ni servicios reales."""

import secrets
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app.api.routes import auth
from app.auth import sessions
from app.auth.local_accounts import LOCAL_ADMIN_ROLE, LOCAL_READER_ROLE, create_local_account, update_local_account
from app.authorization import policy
from app.authorization.context import PERM_USERS_ADMIN
from app.common.errors import ForbiddenError, UnauthorizedError
from app.database.models import (
    ChatOperation,
    LocalCredential,
    Permission,
    Role,
    RolePermission,
    SessionRecord,
    User,
    UserRole,
)
from scripts import local_identity
from tests.unit import test_local_identity_standalone as fixtures

local_system = fixtures.local_system
login = fixtures.login


def create_account(system, *, username="Reader", role=LOCAL_READER_ROLE):
    password = secrets.token_urlsafe(24)
    with system.factory.begin() as db:
        user = create_local_account(db, username=username, display_name=username, password=password, role_name=role)
        return user.id, password


def patch(system, user_id, body, csrf):
    return system.client.patch(f"/api/v1/admin/users/{user_id}", json=body, headers={"X-CSRF-Token": csrf})


@pytest.mark.parametrize("payload", [{}, {"role": None}, {"is_active": None}, {"is_active": "false"},
                                     {"is_active": 0}, {"role": "other"}, {"display_name": "Changed"}])
def test_patch_requires_explicit_strict_allowlisted_fields(local_system, payload):
    profile = login(local_system).json()
    assert patch(local_system, local_system.user_id, payload, profile["csrf_token"]).status_code == 422


def test_patch_requires_current_admin_session_and_csrf(local_system):
    target, password = create_account(local_system)
    endpoint = f"/api/v1/admin/users/{target}"
    assert local_system.client.patch(endpoint, json={"is_active": False}).status_code == 401
    profile = login(local_system).json()
    assert local_system.client.patch(endpoint, json={"is_active": False}).status_code == 403
    assert patch(local_system, target, {"is_active": False}, "wrong-csrf").status_code == 403
    reader = login(local_system, username="Reader", password=password).json()
    assert patch(local_system, target, {"role": LOCAL_ADMIN_ROLE}, reader["csrf_token"]).status_code == 403
    assert PERM_USERS_ADMIN in profile["permissions"]


def test_deactivate_and_reactivate_revokes_old_sessions_without_replacing_credentials(local_system):
    target, password = create_account(local_system)
    admin = login(local_system).json()
    with TestClient(local_system.app) as reader:
        assert login(local_system, username="Reader", password=password, client=reader).status_code == 200
        with local_system.factory() as db:
            digest = db.get(LocalCredential, target).password_hash_argon2id
        response = patch(local_system, target, {"is_active": False}, admin["csrf_token"])
        assert response.status_code == 200 and response.json()["is_active"] is False
        assert "password" not in response.text
        assert reader.get("/api/v1/me").status_code == 401
        assert login(local_system, username="Reader", password=password, client=reader).status_code == 401
        with local_system.factory() as db:
            assert db.get(LocalCredential, target).password_hash_argon2id == digest
            records = db.execute(select(SessionRecord).where(SessionRecord.user_id == target)).scalars().all()
            assert records and all(record.revoked_at is not None for record in records)
        assert patch(local_system, target, {"is_active": True}, admin["csrf_token"]).status_code == 200
        assert reader.get("/api/v1/me").status_code == 401
        assert login(local_system, username="Reader", password=password, client=reader).status_code == 200


def test_role_changes_revoke_sessions_and_next_login_uses_current_permissions(local_system):
    target, password = create_account(local_system)
    admin = login(local_system).json()
    with TestClient(local_system.app) as reader:
        assert login(local_system, username="Reader", password=password, client=reader).status_code == 200
        assert patch(local_system, target, {"role": LOCAL_ADMIN_ROLE}, admin["csrf_token"]).status_code == 200
        assert reader.get("/api/v1/me").status_code == 401
        elevated = login(local_system, username="Reader", password=password, client=reader).json()
        assert PERM_USERS_ADMIN in elevated["permissions"]
        assert patch(local_system, target, {"role": LOCAL_READER_ROLE}, admin["csrf_token"]).status_code == 200
        assert reader.get("/api/v1/me").status_code == 401
        reduced = login(local_system, username="Reader", password=password, client=reader).json()
        assert reduced["permissions"] == [] and reduced["allowed_categories"] == ["prestaciones"]


@pytest.mark.parametrize("payload", [{"is_active": False}, {"role": LOCAL_READER_ROLE},
                                     {"is_active": False, "role": LOCAL_READER_ROLE}])
def test_last_active_local_admin_cannot_be_removed(local_system, payload):
    admin = login(local_system).json()
    response = patch(local_system, local_system.user_id, payload, admin["csrf_token"])
    assert response.status_code == 422
    assert local_system.client.get("/api/v1/me").status_code == 200
    with local_system.factory() as db:
        assert db.get(User, local_system.user_id).is_active
        names = db.execute(select(Role.name).join(UserRole).where(UserRole.user_id == local_system.user_id)).scalars()
        assert list(names) == [LOCAL_ADMIN_ROLE]


@pytest.mark.parametrize("payload", [{"is_active": False}, {"role": LOCAL_READER_ROLE}])
def test_admin_can_relinquish_access_when_another_active_admin_exists(local_system, payload):
    _, password = create_account(local_system, username="SecondAdmin", role=LOCAL_ADMIN_ROLE)
    admin = login(local_system).json()
    assert patch(local_system, local_system.user_id, payload, admin["csrf_token"]).status_code == 200
    assert local_system.client.get("/api/v1/me").status_code == 401
    assert login(local_system, username="SecondAdmin", password=password).status_code == 200


@pytest.mark.parametrize("kind", ["entra", "local_test", "synthetic_local", "missing"])
def test_only_real_local_accounts_can_be_changed(local_system, kind):
    target = "not-present"
    if kind != "missing":
        with local_system.factory.begin() as db:
            user = User(username="Other", display_name="Other", auth_source="local" if kind == "synthetic_local" else kind,
                        is_active=True, is_synthetic_test=kind != "entra")
            db.add(user)
            db.flush()
            target = user.id
    admin = login(local_system).json()
    assert patch(local_system, target, {"is_active": False}, admin["csrf_token"]).status_code == 404
    if kind != "missing":
        with local_system.factory() as db:
            assert db.get(User, target).is_active


@pytest.mark.parametrize("revoke", ["role", "session", "inactive"])
def test_old_signed_admin_context_cannot_authorize_mutation(local_system, revoke):
    actor_id, _ = create_account(local_system, username="SecondAdmin", role=LOCAL_ADMIN_ROLE)
    target, _ = create_account(local_system)
    with local_system.factory.begin() as db:
        actor = db.get(User, actor_id)
        issued = sessions.create_session(db, user=actor, auth_source="local")
        context = policy.get_policy_engine().build_context(db, user=actor, session_id=issued.session_id, request_id="test")
    with local_system.factory.begin() as db:
        if revoke == "session":
            sessions.revoke_session(db, issued.session_id)
        else:
            update_local_account(db, user_id=actor_id, role_name=LOCAL_READER_ROLE if revoke == "role" else None,
                                 is_active=False if revoke == "inactive" else None)
    with local_system.factory.begin() as db, pytest.raises((ForbiddenError, UnauthorizedError)):
        update_local_account(db, user_id=target, is_active=False, actor=context)
    with local_system.factory() as db:
        assert db.get(User, target).is_active


@pytest.mark.parametrize("payload", [{"is_active": False}, {"role": LOCAL_READER_ROLE}])
def test_queued_work_rejects_session_after_account_change(local_system, monkeypatch, payload):
    from app.agents import chat_queue

    target, _ = create_account(local_system, username="SecondAdmin", role=LOCAL_ADMIN_ROLE)
    monkeypatch.setattr(chat_queue, "get_settings", lambda: local_system.settings)
    monkeypatch.setattr(chat_queue, "get_policy_engine", policy.get_policy_engine)
    with local_system.factory.begin() as db:
        user = db.get(User, target)
        issued = sessions.create_session(db, user=user, auth_source="local")
        ctx = policy.get_policy_engine().build_context(db, user=user, session_id=issued.session_id, request_id="queued")
        operation = ChatOperation(id="synthetic-operation", user_id=target, session_id=issued.session_id,
                                  request_id="queued", authorization_scope=chat_queue.authorization_fingerprint(
                                      db, ctx, policy.get_policy_engine().effective_categories(ctx),
                                  ))
        assert chat_queue.context_for_operation(db, operation).user_id == target
    admin = login(local_system).json()
    assert patch(local_system, target, payload, admin["csrf_token"]).status_code == 200
    with local_system.factory() as db, pytest.raises(UnauthorizedError):
        chat_queue.context_for_operation(db, operation)


def test_commit_failure_cannot_return_success_or_leave_partial_deactivation(local_system, monkeypatch):
    target, password = create_account(local_system)
    admin = login(local_system).json()
    actual = auth.get_audit_service()

    def audit(db, *args, **kwargs):
        actual.record_from_context(db, *args, **kwargs)
        monkeypatch.setattr(db, "commit", Mock(side_effect=OperationalError("synthetic", {}, Exception("failure"))))

    monkeypatch.setattr(auth, "get_audit_service", lambda: type("Audit", (), {"record_from_context": staticmethod(audit)})())
    with TestClient(local_system.app, raise_server_exceptions=False) as client:
        client.cookies.update(local_system.client.cookies)
        response = client.patch(f"/api/v1/admin/users/{target}", json={"is_active": False},
                                headers={"X-CSRF-Token": admin["csrf_token"]})
        assert response.status_code == 500
    monkeypatch.setattr(auth, "get_audit_service", lambda: actual)
    with local_system.factory() as db:
        assert db.get(User, target).is_active
    assert login(local_system, username="Reader", password=password).status_code == 200


@pytest.mark.parametrize("username", ["LateAccount", "Matrix"])
def test_create_revalidates_admin_revoked_after_dependencies(local_system, monkeypatch, username):
    create_account(local_system, username="SecondAdmin", role=LOCAL_ADMIN_ROLE)
    admin = login(local_system).json()
    original_create = auth.create_local_account

    def changed_before_lock(db, **kwargs):
        # La frontera debe liberar el lock last_seen de dependencias antes de
        # competir; otra transaccion ya puede cambiar al administrador.
        assert not db.in_transaction()
        with local_system.factory.begin() as other:
            update_local_account(other, user_id=local_system.user_id, role_name=LOCAL_READER_ROLE)
        return original_create(db, **kwargs)

    monkeypatch.setattr(auth, "create_local_account", changed_before_lock)
    response = local_system.client.post("/api/v1/admin/users", json={
        "username": username, "display_name": "LateAccount", "password": secrets.token_urlsafe(24),
        "role": LOCAL_ADMIN_ROLE,
    }, headers={"X-CSRF-Token": admin["csrf_token"]})
    assert response.status_code == 403
    with local_system.factory() as db:
        assert db.scalar(select(User.id).where(User.username == "LateAccount")) is None


def test_create_commit_failure_cannot_return_created(local_system, monkeypatch):
    admin = login(local_system).json()
    actual = auth.get_audit_service()

    def audit(db, *args, **kwargs):
        actual.record_from_context(db, *args, **kwargs)
        monkeypatch.setattr(db, "commit", Mock(side_effect=OperationalError("synthetic", {}, Exception("failure"))))

    monkeypatch.setattr(auth, "get_audit_service", lambda: type("Audit", (), {"record_from_context": staticmethod(audit)})())
    with TestClient(local_system.app, raise_server_exceptions=False) as client:
        client.cookies.update(local_system.client.cookies)
        response = client.post("/api/v1/admin/users", json={
            "username": "FailedAccount", "display_name": "FailedAccount", "password": secrets.token_urlsafe(24),
        }, headers={"X-CSRF-Token": admin["csrf_token"]})
        assert response.status_code == 500
    with local_system.factory() as db:
        assert db.scalar(select(User.id).where(User.username == "FailedAccount")) is None


def test_create_does_not_reprovision_revoked_permission(local_system, monkeypatch):
    admin = login(local_system).json()
    original_create = auth.create_local_account
    with local_system.factory() as db:
        role_id = db.scalar(select(Role.id).where(Role.name == LOCAL_ADMIN_ROLE))
        permission_id = db.scalar(select(Permission.id).where(Permission.name == PERM_USERS_ADMIN))

    def revoke_before_lock(db, **kwargs):
        assert not db.in_transaction()
        with local_system.factory.begin() as other:
            other.execute(delete(RolePermission).where(RolePermission.role_id == role_id,
                                                       RolePermission.permission_id == permission_id))
        return original_create(db, **kwargs)

    monkeypatch.setattr(auth, "create_local_account", revoke_before_lock)
    response = local_system.client.post("/api/v1/admin/users", json={
        "username": "LateAccount", "display_name": "LateAccount", "password": secrets.token_urlsafe(24),
    }, headers={"X-CSRF-Token": admin["csrf_token"]})
    assert response.status_code == 403
    with local_system.factory() as db:
        assert db.get(RolePermission, (role_id, permission_id)) is None
        assert db.scalar(select(User.id).where(User.username == "LateAccount")) is None


def test_cli_lifecycle_requires_no_password_and_preserves_other_admin(local_system, monkeypatch, capsys):
    target, password = create_account(local_system)
    login(local_system, username="Reader", password=password)
    monkeypatch.setattr(local_identity, "session_scope", local_system.factory.begin)
    monkeypatch.setattr(local_identity, "read_password", Mock(side_effect=AssertionError("No password prompt allowed")))
    for args in (["set-role", "--username", "Reader", "--role", LOCAL_ADMIN_ROLE],
                 ["set-active", "--username", "Reader", "--inactive"],
                 ["set-active", "--username", "Reader", "--active"],
                 ["set-role", "--username", "Reader", "--role", LOCAL_READER_ROLE]):
        assert local_identity.main(args) == 0
    assert password not in capsys.readouterr().out
    assert local_system.client.get("/api/v1/me").status_code == 401
    with local_system.factory() as db:
        assert db.get(User, target).is_active
    assert local_identity.main(["set-active", "--username", "Matrix", "--inactive"]) == 1
    with local_system.factory() as db:
        assert db.get(User, local_system.user_id).is_active
