# Creado por Aldo Garcia.
"""Errores uniformes y backoff acotado sin bloquear al titular de una cuenta."""

import secrets
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import auth
from app.auth import local_provider, sessions
from app.auth.passwords import verify_password
from app.common.errors import UnauthorizedError
from app.common.ids import utcnow_naive
from app.database.models import AuditEvent, LocalCredential, SessionRecord, User
from app.security import rate_limit
from app.security.rate_limit import RateLimiter, get_rate_limiter
from seeds.identity_seed import SYNTHETIC_TEST_PASSWORD
from tests.unit.test_local_identity_standalone import local_system, login

# La clave sintetica central tiene ocho caracteres; nunca coincide con la
# credencial aleatoria de 32 caracteres generada por local_system.
__all__ = ["local_system"]


@pytest.mark.parametrize("state", ["unknown", "inactive", "legacy_lock", "wrong", "missing_credential"])
def test_all_invalid_accounts_use_same_401_and_progressive_failure_delay(local_system, monkeypatch, state):
    delays = []
    monkeypatch.setattr(auth.time, "sleep", delays.append)
    with local_system.factory.begin() as db:
        user = db.get(User, local_system.user_id)
        credential = db.get(LocalCredential, local_system.user_id)
        if state == "inactive":
            user.is_active = False
        elif state == "legacy_lock":
            credential.locked_until = utcnow_naive() + timedelta(minutes=5)
        elif state == "missing_credential":
            db.delete(credential)
    username = "UnknownSynthetic" if state == "unknown" else "Matrix"
    payloads = []
    for _ in range(6):
        response = login(local_system, username=username, password=SYNTHETIC_TEST_PASSWORD)
        assert response.status_code == 401
        payloads.append(response.json())
    assert all(value == payloads[0] for value in payloads)
    assert payloads[0]["code"] == "unauthorized"
    assert payloads[0]["message"] == "Usuario o contrasena incorrectos."
    assert delays == [0.25, 0.5, 1.0, 2.0, 2.0, 2.0]


def test_success_clears_failure_backoff_and_never_delays_valid_credentials(local_system, monkeypatch):
    delays = []
    monkeypatch.setattr(auth.time, "sleep", delays.append)
    for _ in range(7):
        assert login(local_system, password=SYNTHETIC_TEST_PASSWORD).status_code == 401
    assert delays[-1] == 2
    with local_system.factory() as db:
        assert db.get(LocalCredential, local_system.user_id).failed_attempts == 5
    count = len(delays)
    assert login(local_system).status_code == 200
    assert len(delays) == count
    assert login(local_system, password=SYNTHETIC_TEST_PASSWORD).status_code == 401
    assert delays[-1] == 0.25


def test_backoff_waits_only_after_commit_has_released_the_database_transaction(local_system, monkeypatch):
    observed = []
    request = SimpleNamespace(state=SimpleNamespace(request_id="synthetic-backoff-request"))
    with local_system.factory() as db:
        def wait(seconds):
            assert not db.in_transaction()
            with local_system.factory() as other:
                assert other.get(LocalCredential, local_system.user_id).failed_attempts == 1
                assert other.execute(select(AuditEvent)).first() is not None
            observed.append(seconds)

        monkeypatch.setattr(auth.time, "sleep", wait)
        with pytest.raises(UnauthorizedError):
            auth._authenticate_local(db, request, username="Matrix", password=SYNTHETIC_TEST_PASSWORD)
    assert observed == [0.25]


def test_failure_backoff_is_case_insensitive_bounded_and_expires(local_system, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
    limiter = RateLimiter(window_seconds=300, max_keys=1)
    monkeypatch.setattr(auth, "_failed_logins", limiter)
    delays = []
    monkeypatch.setattr(auth.time, "sleep", delays.append)
    for value in (" Matrix ", "matrix", "MATRIX", "Other", "Third"):
        auth._delay_failed_login(value)
    assert delays == [0.25, 0.5, 1.0, 2.0, 2.0]
    assert len(limiter._events) == 1
    clock[0] += 301
    auth._delay_failed_login("Third")
    assert delays[-1] == 0.25
    assert len(limiter._events) == 1


@pytest.mark.parametrize("username", ["Matrix", "UnknownSynthetic"])
def test_ip_limit_still_prevents_password_work_regardless_of_account_existence(local_system, monkeypatch, username):
    local_system.settings.rate_limit_login_per_minute = 2
    work = []
    monkeypatch.setattr(local_provider, "verify_password", lambda *_: work.append("real") or False)
    monkeypatch.setattr(local_provider, "dummy_verify", lambda: work.append("dummy"))
    get_rate_limiter().reset()
    responses = [login(local_system, username=username, password=SYNTHETIC_TEST_PASSWORD) for _ in range(3)]
    assert [response.status_code for response in responses] == [401, 401, 429]
    assert len(work) == 2


@pytest.mark.parametrize("alias", ["Mátrix", "Ｍatrix", "Matri\u0301x", "Matrıx", "Matrix\x00"])
def test_local_username_aliases_outside_registration_contract_never_reach_sql(local_system, monkeypatch, alias):
    checks = []
    monkeypatch.setattr(local_provider, "dummy_verify", lambda: checks.append("dummy"))

    def forbidden_query(*_args, **_kwargs):
        raise AssertionError("Identificador local invalido no debe consultar SQL")

    with local_system.factory() as db:
        monkeypatch.setattr(db, "execute", forbidden_query)
        with pytest.raises(UnauthorizedError, match="Usuario o contrasena incorrectos"):
            local_provider.LocalIdentityProvider().authenticate(db, username=alias, password=local_system.password)
    assert checks == ["dummy"]


def test_attack_from_one_ip_does_not_lock_valid_owner_on_another_ip(local_system):
    local_system.settings.rate_limit_login_per_minute = 5
    with TestClient(local_system.app, client=("192.0.2.10", 1234)) as attacker:
        for _ in range(5):
            assert login(local_system, client=attacker, password=SYNTHETIC_TEST_PASSWORD).status_code == 401
        assert login(local_system, client=attacker, password=SYNTHETIC_TEST_PASSWORD).status_code == 429
        with TestClient(local_system.app, client=("192.0.2.20", 1234)) as owner:
            assert login(local_system, client=owner).status_code == 200
        # El acceso correcto no borra el limite de la IP que envio los errores.
        assert login(local_system, client=attacker, password=SYNTHETIC_TEST_PASSWORD).status_code == 429


def test_password_change_releases_dependency_locks_and_rejects_intermediate_session_revocation(
    local_system, monkeypatch,
):
    profile = login(local_system).json()
    next_password = secrets.token_urlsafe(24)
    authenticate = auth._authenticate_local
    observed = []

    def revoked_before_reauthentication(db, request, **kwargs):
        assert not db.in_transaction()
        with local_system.factory.begin() as other:
            record = other.execute(select(SessionRecord).where(
                SessionRecord.user_id == local_system.user_id, SessionRecord.revoked_at.is_(None),
            )).scalar_one()
            sessions.revoke_session(other, record.id)
        observed.append("revoked")
        return authenticate(db, request, **kwargs)

    monkeypatch.setattr(auth, "_authenticate_local", revoked_before_reauthentication)
    response = local_system.client.post("/api/v1/auth/local/change-password", json={
        "current_password": local_system.password, "new_password": next_password,
    }, headers={"X-CSRF-Token": profile["csrf_token"]})
    assert response.status_code == 401
    assert observed == ["revoked"]
    with local_system.factory() as db:
        stored = db.get(LocalCredential, local_system.user_id).password_hash_argon2id
        assert verify_password(stored, local_system.password)
        assert not verify_password(stored, next_password)
