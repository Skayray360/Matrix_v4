# Creado por Aldo Garcia.
"""Ataques HTTP locales sin servicios externos ni datos de usuarios reales."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api import body_limit
from app.config import get_settings
from app.main import create_app
from app.security.rate_limit import RateLimiter

pytestmark = pytest.mark.security


@pytest.fixture
def client(monkeypatch):
    settings = get_settings().model_copy(update={
        "http_body_max_bytes": 1024, "http_body_timeout_seconds": 1.0,
        "upload_body_max_bytes": 2048,
    })
    monkeypatch.setattr(body_limit, "get_settings", lambda: settings)
    application = create_app()

    @application.post("/__security/echo")
    async def echo(payload: dict):
        return {"accepted": True}

    @application.post("/__security/failure")
    async def failure():
        raise RuntimeError("synthetic-private-error-marker")

    @application.post("/__security/body")
    async def raw_body(request: Request):
        return {"size": len(await request.body())}

    return TestClient(application, raise_server_exceptions=False)


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", "application/octet-stream", ""])
def test_oversized_non_multipart_body_is_rejected_before_route(client, content_type):
    headers = {"Content-Type": content_type} if content_type else {}
    response = client.post("/__security/body", content=b"x" * 2049, headers=headers)
    assert response.status_code == 413
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers.get("X-Request-ID")


@pytest.mark.parametrize("host", ["attacker.invalid", "127.0.0.1.attacker.invalid", "attacker.invalid:8000"])
def test_host_cannot_rebind_a_foreign_origin_to_loopback(client, host):
    response = client.get("/health", headers={"Host": host})
    assert response.status_code == 400


@pytest.mark.parametrize("origin", ["https://attacker.invalid", "null", "http://testserver:9999"])
def test_unsafe_cross_origin_request_is_rejected_even_without_session(client, origin):
    response = client.post("/__security/echo", json={}, headers={"Origin": origin})
    assert response.status_code == 403


def test_same_origin_and_non_browser_clients_remain_supported(client):
    assert client.post("/__security/echo", json={}, headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/__security/echo", json={}).status_code == 200


def test_security_headers_and_correlation_survive_unhandled_errors(client):
    response = client.post("/__security/failure")
    assert response.status_code == 500
    assert "synthetic-private-error-marker" not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


@pytest.mark.asyncio
async def test_chunked_body_is_limited_without_trusting_content_length(monkeypatch):
    settings = get_settings().model_copy(update={"http_body_max_bytes": 1024})
    monkeypatch.setattr(body_limit, "get_settings", lambda: settings)
    messages = iter([
        {"type": "http.request", "body": b"x" * 700, "more_body": True},
        {"type": "http.request", "body": b"x" * 700, "more_body": False},
    ])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def route(scope, receive, send):
        while (await receive()).get("more_body", False):
            pass
        pytest.fail("El cuerpo excesivo no debe alcanzar la ruta")

    await body_limit.UploadBodyLimitMiddleware(route)(
        {"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")]}, receive, send,
    )
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_slow_body_times_out_before_parsing(monkeypatch):
    settings = get_settings().model_copy(update={"http_body_timeout_seconds": 0.01})
    monkeypatch.setattr(body_limit, "get_settings", lambda: settings)
    sent = []

    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    async def route(*args):
        pytest.fail("La ruta no debe esperar un cuerpo lento")

    await body_limit.UploadBodyLimitMiddleware(route)(
        {"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")]}, receive, send,
    )
    assert sent[0]["status"] == 408


def test_unique_rate_limit_keys_cannot_grow_memory_or_evict_live_limits(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.security.rate_limit.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(window_seconds=60, max_keys=10)
    assert limiter.check("protected", limit=1).allowed
    for index in range(100):
        limiter.check(f"attacker-{index}", limit=1)
    assert len(limiter._events) <= 10
    assert not limiter.check("protected", limit=1).allowed
    assert not limiter.check("newcomer", limit=1).allowed
    clock[0] = 61.0
    assert limiter.check("newcomer", limit=1).allowed
    assert len(limiter._events) == 1


@pytest.mark.parametrize("headers", [
    [(b"content-length", b"1"), (b"content-length", b"2")],
    [(b"content-length", b"1"), (b"transfer-encoding", b"chunked")],
    [(b"content-length", b"-1")],
    [(b"content-length", b"1, 1")],
    [(b"content-length", b"9" * 100)],
    [(b"content-type", b"application/json"), (b"content-type", b"text/plain")],
])
@pytest.mark.asyncio
async def test_ambiguous_request_framing_is_rejected_without_reading(headers):
    sent = []

    async def receive():
        pytest.fail("El encuadre ambiguo no debe consumir el cuerpo")

    async def send(message):
        sent.append(message)

    async def route(*args):
        pytest.fail("El parser no debe recibir un encuadre ambiguo")

    await body_limit.UploadBodyLimitMiddleware(route)(
        {"type": "http", "method": "POST", "headers": headers}, receive, send,
    )
    assert sent[0]["status"] == 400


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_loopback_authorities_work_with_ipv4_ipv6_and_ports(client, host):
    assert client.get("/health", headers={"Host": host + ":8000"}).status_code == 200


@pytest.mark.parametrize("host", ["localhost@attacker.invalid", "localhost/path", "localhost:", "localhost:bad"])
def test_malformed_authority_is_rejected_without_internal_error(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 400


def test_vite_same_origin_proxy_and_public_reverse_proxy_still_work(client):
    for host, origin in [("localhost:5173", "http://localhost:5173"), ("127.0.0.1:8085", "http://127.0.0.1:8085")]:
        assert client.post("/__security/echo", json={}, headers={"Host": host, "Origin": origin}).status_code == 200


def test_cross_site_fetch_is_rejected_even_if_origin_is_omitted(client):
    response = client.post("/__security/echo", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_forwarded_host_cannot_whitelist_an_attacker(client):
    response = client.get("/health", headers={"Host": "attacker.invalid", "X-Forwarded-Host": "127.0.0.1"})
    assert response.status_code == 400


def test_federated_login_creation_is_rate_limited(monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.api.routes import auth
    from app.common.errors import RateLimitedError
    from app.config import AuthProvider
    from app.security.rate_limit import get_rate_limiter

    limiter = get_rate_limiter()
    limiter.reset()
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(
        auth_provider=AuthProvider.ENTRA, rate_limit_login_per_minute=2, session_cookie_secure=True,
    ))
    starts = []

    def start_login(db):
        starts.append(True)
        return SimpleNamespace(authorization_url="https://identity.invalid/auth", state="synthetic-state")

    monkeypatch.setattr(auth, "get_identity_provider", lambda: SimpleNamespace(start_login=start_login))
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 9000)})
    try:
        auth.login(request, db=None)
        auth.login(request, db=None)
        with pytest.raises(RateLimitedError):
            auth.login(request, db=None)
        assert len(starts) == 2
    finally:
        limiter.reset()


def test_non_ascii_oidc_state_is_unauthorized_instead_of_500(monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request
    from starlette.responses import Response

    from app.api.routes import auth
    from app.common.errors import UnauthorizedError
    from app.config import AuthProvider

    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_provider=AuthProvider.ENTRA))
    request = Request({"type": "http", "headers": [(b"cookie", b"matrixrh_oidc_state=synthetic")]})
    with pytest.raises(UnauthorizedError):
        auth.auth_callback(request, Response(), code="synthetic", state="ñ", db=None)
