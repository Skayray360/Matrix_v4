# Creado por Aldo Garcia.
"""Readiness publico minimo y detalle autenticado, con SQL y sondas sinteticos."""

from types import SimpleNamespace

import pytest

from app.api import deps
from app.api.routes import health
from app.api.schemas import ReadyComponent, ReadyResponse
from app.auth.local_accounts import LOCAL_READER_ROLE, create_local_account
from app.auth.sessions import create_session
from app.database.models import User
from tests.unit import test_local_identity_standalone as identity_fixtures

pytestmark = pytest.mark.security
local_system = identity_fixtures.local_system


@pytest.fixture
def readiness_system(local_system, monkeypatch):
    system = local_system
    system.app.include_router(health.router)
    system.app.include_router(health.router, prefix="/api/v1")
    cache = health.ReadinessCache()
    monkeypatch.setattr(health, "_ready_cache", cache)
    state = SimpleNamespace(ready=True, calls=0)

    def probe(_response):
        state.calls += 1
        return ReadyResponse(ready=state.ready, components=[
            ReadyComponent(name="database", ok=state.ready, detail="mysql-synthetic-8.4"),
            ReadyComponent(name="qdrant", ok=True, detail="C:/private-synthetic/index"),
            ReadyComponent(name="ollama", ok=True, detail="synthetic-model-name"),
        ])

    monkeypatch.setattr(health, "_probe_ready", probe)
    with system.factory.begin() as db:
        admin = db.get(User, system.user_id)
        reader = create_local_account(
            db, username="ReadinessReader", display_name="Synthetic Reader",
            password=system.password, role_name=LOCAL_READER_ROLE,
        )
        tokens = {
            "admin": create_session(db, user=admin, auth_source="local").session_token,
            "reader": create_session(db, user=reader, auth_source="local").session_token,
        }
    return SimpleNamespace(system=system, client=system.client, cache=cache, state=state, tokens=tokens)


def set_cookie(env, identity):
    env.client.cookies.clear()
    if identity != "anonymous":
        env.client.cookies.set(env.system.settings.session_cookie_name,
                               env.tokens.get(identity, "invalid-synthetic-cookie"))


@pytest.mark.parametrize("path", ["/ready", "/api/v1/ready"])
@pytest.mark.parametrize("is_ready", [True, False])
@pytest.mark.parametrize("identity", ["anonymous", "invalid"])
def test_public_readiness_never_resolves_cookies_or_exposes_details(
    readiness_system, monkeypatch, path, is_ready, identity,
):
    env = readiness_system
    env.state.ready = is_ready
    set_cookie(env, identity)
    # El supervisor sigue operativo sin autenticar ni abrir SQL de sesiones.
    monkeypatch.setattr(deps, "get_sessionmaker", lambda: pytest.fail("public readiness attempted authentication"))
    response = env.client.get(path)
    assert response.status_code == (200 if is_ready else 503)
    assert response.json() == {"ready": is_ready}
    assert response.headers["cache-control"] == "no-store"
    assert env.state.calls == 1


@pytest.mark.parametrize("path", ["/ready/details", "/api/v1/ready/details"])
@pytest.mark.parametrize("identity,expected", [("anonymous", 401), ("invalid", 401), ("reader", 403)])
def test_readiness_detail_rejects_before_probing(readiness_system, path, identity, expected):
    env = readiness_system
    set_cookie(env, identity)
    response = env.client.get(path)
    assert response.status_code == expected
    assert env.state.calls == 0
    assert "private-synthetic" not in response.text
    assert "synthetic-model-name" not in response.text


@pytest.mark.parametrize("is_ready", [True, False])
def test_authorized_detail_cannot_contaminate_shared_public_cache(readiness_system, is_ready):
    env = readiness_system
    env.state.ready = is_ready
    set_cookie(env, "admin")
    detailed = env.client.get("/ready/details")
    assert detailed.status_code == (200 if is_ready else 503)
    assert detailed.headers["cache-control"] == "no-store"
    assert detailed.json()["components"][1]["detail"] == "C:/private-synthetic/index"
    set_cookie(env, "anonymous")
    public = env.client.get("/api/v1/ready")
    assert public.status_code == detailed.status_code
    assert public.json() == {"ready": is_ready}
    assert env.state.calls == 1


def test_busy_public_probe_keeps_retry_after_without_internal_components(readiness_system):
    env = readiness_system
    assert env.cache._lock.acquire(blocking=False)
    try:
        response = env.client.get("/ready")
    finally:
        env.cache._lock.release()
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"ready": False}
    assert env.state.calls == 0
    assert env.client.get("/ready").json() == {"ready": True}
