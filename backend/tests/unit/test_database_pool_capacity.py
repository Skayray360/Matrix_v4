# Creado por Aldo Garcia.
"""Pool SQL real acotado y HTTP real; datos temporales, sin servicios externos."""

from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.main import create_app


@pytest.fixture
def pool_api(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'capacity.sqlite'}", connect_args={"check_same_thread": False},
        pool_size=1, max_overflow=0, pool_timeout=.01,
    )
    with engine.begin() as db:
        db.execute(text("CREATE TABLE synthetic_events (id INTEGER PRIMARY KEY)"))
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(deps, "get_sessionmaker", lambda: factory)
    app = create_app()

    @app.post("/__test/capacity")
    def use_database(db: Session = Depends(deps.get_db)):
        db.execute(text("INSERT INTO synthetic_events DEFAULT VALUES"))
        return {"ok": True}

    @app.post("/__test/rollback")
    def rollback_before_reject(db: Session = Depends(deps.get_db)):
        db.execute(text("INSERT INTO synthetic_events DEFAULT VALUES"))
        raise SQLAlchemyTimeoutError("synthetic-private-sql-and-connection-marker")

    @app.post("/__test/unrelated-timeout")
    def unrelated_timeout():
        raise TimeoutError("synthetic-non-database-timeout")

    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield engine, client
    finally:
        client.close()
        engine.dispose()


def assert_capacity_response(response):
    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert response.headers["Retry-After"] == "5"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert response.json()["request_id"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "QueuePool" not in response.text and "sqlite" not in response.text
    assert "synthetic-private" not in response.text


def test_actual_pool_exhaustion_is_retryable_and_recovers_without_leaking_connections(pool_api):
    engine, client = pool_api
    with engine.connect():
        # Checkout TimeoutError real, sin rafaga ni inferencia ni mock del pool.
        responses = [client.post("/__test/capacity", json={}) for _ in range(3)]
        for response in responses:
            assert_capacity_response(response)
        assert len({response.json()["request_id"] for response in responses}) == 3
        assert engine.pool.checkedout() == 1
    assert engine.pool.checkedout() == 0
    assert client.post("/__test/capacity", json={}).status_code == 200
    with engine.connect() as observer:
        assert observer.scalar(text("SELECT count(*) FROM synthetic_events")) == 1
    assert engine.pool.checkedout() == 0


def test_rejected_transaction_rolls_back_and_does_not_log_driver_message(pool_api, caplog):
    engine, client = pool_api
    response = client.post("/__test/rollback", json={})
    assert_capacity_response(response)
    assert "synthetic-private-sql-and-connection-marker" not in caplog.text
    assert "db.pool_capacity_exhausted" in caplog.text
    with engine.connect() as observer:
        assert observer.scalar(text("SELECT count(*) FROM synthetic_events")) == 0
    assert engine.pool.checkedout() == 0


def test_unrelated_timeout_is_not_mislabeled_as_database_capacity(pool_api):
    _, client = pool_api
    response = client.post("/__test/unrelated-timeout", json={})
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "Retry-After" not in response.headers
