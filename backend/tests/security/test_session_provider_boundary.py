# Creado por Aldo Garcia.
"""Las sesiones antiguas no sobreviven a deshabilitar su proveedor de identidad."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import sessions
from app.common.errors import UnauthorizedError
from app.common.ids import sha256_text, utcnow_naive
from app.config import AppEnv, AuthProvider
from app.database.models import SessionRecord, User

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture
def session_store():
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    SessionRecord.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        now = utcnow_naive()
        user = User(
            id="provider-boundary-user", username="synthetic-session-user", display_name="Fixture",
            auth_source="local_test", is_active=True, is_synthetic_test=True,
        )
        record = SessionRecord(
            id="provider-boundary-session", user_id=user.id, auth_source="local_test",
            session_token_hash=sha256_text("synthetic-session-cookie"), csrf_token="synthetic-csrf",
            created_at=now, last_seen_at=now, expires_at=now + timedelta(hours=1),
        )
        db.add_all([user, record])
        db.commit()
        yield db, user, record
    engine.dispose()


def configure(monkeypatch, provider, *, app_env=AppEnv.TEST, enabled=True):
    monkeypatch.setattr(sessions, "get_settings", lambda: SimpleNamespace(
        auth_provider=provider, app_env=app_env, local_test_auth_enabled=enabled, session_idle_minutes=30,
    ))


def resolve(db, record, transport):
    if transport == "cookie":
        return sessions.resolve_session(db, "synthetic-session-cookie")
    return sessions.resolve_session_id(db, record.id)


@pytest.mark.parametrize("transport", ["cookie", "worker"])
@pytest.mark.parametrize("provider", [AuthProvider.LOCAL, AuthProvider.ENTRA, AuthProvider.OIDC])
def test_disabling_test_provider_rejects_existing_sessions(session_store, monkeypatch, provider, transport):
    db, _, record = session_store
    configure(monkeypatch, provider, app_env=AppEnv.PRODUCTION, enabled=False)
    previous = record.last_seen_at
    with pytest.raises(UnauthorizedError):
        resolve(db, record, transport)
    assert record.last_seen_at == previous


@pytest.mark.parametrize("transport", ["cookie", "worker"])
@pytest.mark.parametrize("previous_provider,current_provider", [
    (AuthProvider.ENTRA, AuthProvider.OIDC),
    (AuthProvider.LOCAL, AuthProvider.ENTRA),
    (AuthProvider.OIDC, AuthProvider.LOCAL),
])
def test_provider_migration_requires_new_authentication(
    session_store, monkeypatch, previous_provider, current_provider, transport,
):
    db, user, record = session_store
    user.auth_source = record.auth_source = previous_provider.value
    user.is_synthetic_test = False
    db.commit()
    configure(monkeypatch, current_provider, app_env=AppEnv.PRODUCTION, enabled=False)
    with pytest.raises(UnauthorizedError):
        resolve(db, record, transport)


@pytest.mark.parametrize("transport", ["cookie", "worker"])
@pytest.mark.parametrize("provider", list(AuthProvider))
def test_active_provider_accepts_consistent_session(session_store, monkeypatch, provider, transport):
    db, user, record = session_store
    user.auth_source = record.auth_source = provider.value
    user.is_synthetic_test = provider is AuthProvider.LOCAL_TEST
    db.commit()
    configure(monkeypatch, provider)
    assert resolve(db, record, transport) == (record, user)


@pytest.mark.parametrize("transport", ["cookie", "worker"])
def test_identity_source_change_rejects_existing_session(session_store, monkeypatch, transport):
    db, user, record = session_store
    user.auth_source = "local"
    db.commit()
    configure(monkeypatch, AuthProvider.LOCAL_TEST)
    with pytest.raises(UnauthorizedError):
        resolve(db, record, transport)


@pytest.mark.parametrize("provider", [AuthProvider.LOCAL, AuthProvider.ENTRA, AuthProvider.OIDC])
def test_synthetic_identity_cannot_use_real_provider_session(session_store, monkeypatch, provider):
    db, user, record = session_store
    user.auth_source = record.auth_source = provider.value
    db.commit()
    configure(monkeypatch, provider, app_env=AppEnv.PRODUCTION, enabled=False)
    with pytest.raises(UnauthorizedError):
        sessions.resolve_session(db, "synthetic-session-cookie")


@pytest.mark.parametrize("app_env,enabled", [(AppEnv.PRODUCTION, True), (AppEnv.STAGING, True), (AppEnv.TEST, False)])
def test_test_session_requires_enabled_test_environment(session_store, monkeypatch, app_env, enabled):
    db, _, record = session_store
    configure(monkeypatch, AuthProvider.LOCAL_TEST, app_env=app_env, enabled=enabled)
    with pytest.raises(UnauthorizedError):
        sessions.resolve_session_id(db, record.id)
