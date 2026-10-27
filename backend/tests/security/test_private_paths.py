# Creado por Aldo Garcia.
"""Regresion: dotfiles sinteticos dentro del build nunca deben publicarse."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main

pytestmark = pytest.mark.security

SPA = "<html>synthetic-spa-marker</html>"
PRIVATE = "synthetic-private-marker"
PUBLIC = "synthetic-public-marker"


@pytest.fixture
def frontend_client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    for name in (".git/HEAD", ".env", "nested/.git/config", "nested/.secret"):
        private_file = dist / "assets" / name
        private_file.parent.mkdir(parents=True, exist_ok=True)
        private_file.write_text(PRIVATE, encoding="utf-8")
    (dist / "assets/app.hash.js").write_text(PUBLIC, encoding="utf-8")
    (dist / "index.html").write_text(SPA, encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    # No lifespan: estas pruebas no arrancan jobs ni conectan MySQL/Qdrant/Ollama.
    return TestClient(main.create_app())


@pytest.mark.parametrize("path", [
    "/.git/HEAD", "/.env", "/nested/.git/config", "/.well-known/test",
    "/assets/.git/HEAD", "/assets/.env", "/assets/nested/.git/config", "/assets/nested/.secret",
    "/%2egit/HEAD", "/assets/%2egit/HEAD", "/assets/%2Eenv",
    "/assets/nested%2f.git/config", "/assets/nested%5c.git/config",
    "/assets/%5c.git%5cHEAD", "/assets//.git/HEAD", "/api/v1/.env",
])
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "OPTIONS"])
def test_hidden_paths_are_rejected_before_static_files_and_spa(frontend_client, path, method):
    response = frontend_client.request(method, path)
    assert response.status_code == 404
    assert PRIVATE not in response.text
    assert SPA not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers.get("X-Request-ID")
    if method != "HEAD":
        assert response.json()["message"] == "Recurso no encontrado."


@pytest.mark.parametrize("path", ["/", "/chat", "/conversations/item.v2", "/chat?next=/.git/HEAD"])
def test_public_navigation_and_query_strings_remain_supported(frontend_client, path):
    response = frontend_client.get(path)
    assert response.status_code == 200
    assert response.text == SPA


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_public_assets_remain_supported(frontend_client, method):
    response = frontend_client.request(method, "/assets/app.hash.js")
    assert response.status_code == 200
    assert response.text == (PUBLIC if method == "GET" else "")


def test_health_and_api_misses_keep_their_contract(frontend_client):
    assert frontend_client.get("/health").status_code == 200
    assert frontend_client.get("/api/v1/unknown").status_code == 404


def test_hidden_paths_are_rejected_without_a_frontend_build(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path / "missing")
    response = TestClient(main.create_app()).get("/.git/HEAD")
    assert response.status_code == 404
    assert response.json()["message"] == "Recurso no encontrado."
