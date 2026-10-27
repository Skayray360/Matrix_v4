# Creado por Aldo Garcia.
"""La conexion a Qdrant local no delega credenciales a proxies del equipo."""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.rag.vector_store import VectorStore

pytestmark = pytest.mark.unit


def test_server_client_ignores_host_proxies_and_never_starts_auxiliary_version_request(monkeypatch):
    # Un proxy SOCKS no soportado haria fallar la construccion de HTTPX si se
    # heredara el entorno. No se abre conexion alguna ni se necesita socksio.
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        monkeypatch.setenv(name, "socks5://proxy.example.invalid:9999")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    settings = Settings(_env_file=None, app_env="test", qdrant_mode="server",
                        qdrant_url="http://127.0.0.1:6333")
    monkeypatch.setattr("app.rag.vector_store.get_settings", lambda: settings)
    monkeypatch.setattr("qdrant_client.qdrant_remote.Thread",
                        lambda *a, **kw: pytest.fail("El sondeo auxiliar usa HTTPX fuera del transporte privado."))
    store = VectorStore()
    store.close()
