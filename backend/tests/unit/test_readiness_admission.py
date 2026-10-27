# Creado por Aldo Garcia.
"""Readiness probes are bounded even when unauthenticated callers poll together."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.api.routes import health
from app.api.schemas import ReadyComponent, ReadyResponse


@pytest.fixture
def probes(monkeypatch):
    # No database, identity service, runtime or corpus is contacted.
    monkeypatch.setattr("app.llm.ollama_client.get_ollama_client", lambda: SimpleNamespace(ping=lambda: False))
    monkeypatch.setattr("app.rag.vector_store.get_vector_store", lambda: SimpleNamespace(health=lambda: (False, "offline")))
    monkeypatch.setattr("app.auth.provider.get_identity_provider", lambda: SimpleNamespace(
        name="synthetic", status=lambda: "ERROR",
    ))
    if hasattr(health, "ReadinessCache"):
        monkeypatch.setattr(health, "_ready_cache", health.ReadinessCache())


def test_concurrent_polls_run_only_one_probe_and_do_not_wait(probes, monkeypatch):
    entered, release = Event(), Event()
    counter_lock = Lock()
    calls = []

    def database_probe():
        with counter_lock:
            calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return False, "offline"

    monkeypatch.setattr("app.database.engine.check_database", database_probe)
    with ThreadPoolExecutor(max_workers=9) as pool:
        leader = pool.submit(health.ready, Response())
        assert entered.wait(timeout=2)
        followers = [pool.submit(health.ready, Response()) for _ in range(8)]
        try:
            for follower in followers:
                result = follower.result(timeout=1)
                assert result.ready is False
                assert result.components[0].name == "readiness_probe"
        finally:
            release.set()
        assert leader.result(timeout=2).ready is False
    assert len(calls) == 1


def test_sequential_polls_reuse_brief_cache_but_expire(probes, monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(health, "monotonic", lambda: clock[0], raising=False)

    def database_probe():
        calls.append(1)
        return False, "offline"

    monkeypatch.setattr("app.database.engine.check_database", database_probe)
    first = health.ready(Response())
    for _ in range(50):
        response = Response()
        assert health.ready(response).ready is False
        assert response.status_code == 503
    assert len(calls) == 1
    # A consumer must not mutate the cached result shared with later requests.
    first.components.clear()
    assert health.ready(Response()).components
    clock[0] += 3.0
    health.ready(Response())
    assert len(calls) == 2


def test_health_does_not_depend_on_readiness_cache(monkeypatch):
    monkeypatch.setattr("app.database.engine.check_database", lambda: pytest.fail("liveness touched SQL"))
    assert health.health().status == "ok"


def test_healthy_cache_cannot_hide_failure_past_two_seconds(monkeypatch):
    clock = [0.0]
    healthy = [True]
    cache = health.ReadinessCache()
    monkeypatch.setattr(health, "monotonic", lambda: clock[0])
    monkeypatch.setattr(health, "_probe_ready", lambda _: ReadyResponse(
        ready=healthy[0], components=[ReadyComponent(name="synthetic", ok=healthy[0], detail="synthetic")],
    ))
    response = Response()
    assert cache.check(response).ready and response.status_code == 200
    healthy[0] = False
    clock[0] = 2.0
    response = Response()
    assert not cache.check(response).ready and response.status_code == 503


def test_probe_exception_releases_capacity_without_caching_success(monkeypatch):
    cache = health.ReadinessCache()

    def failure(_):
        raise RuntimeError("synthetic-probe-failure")

    monkeypatch.setattr(health, "_probe_ready", failure)
    with pytest.raises(RuntimeError, match="synthetic-probe-failure"):
        cache.check(Response())
    monkeypatch.setattr(health, "_probe_ready", lambda _: ReadyResponse(ready=False, components=[]))
    assert cache.check(Response()).ready is False
