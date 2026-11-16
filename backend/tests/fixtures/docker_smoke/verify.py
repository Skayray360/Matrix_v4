# Creado por Aldo Garcia.
"""Aceptacion HTTPS del despliegue CI; nunca imprime passwords/cookies/tokens."""

import http.cookiejar
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = "https://127.0.0.1:8443"
context = ssl.create_default_context(cafile=str(ROOT / "knowledge-base/state/docker/tls/matrixrh.crt"))
cookies = http.cookiejar.CookieJar()
client = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=context), urllib.request.HTTPCookieProcessor(cookies),
)


def call(path, *, method="GET", payload=None, csrf=None):
    headers = {"Origin": BASE}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(BASE + path, method=method, headers=headers,
                                     data=None if payload is None else json.dumps(payload).encode())
    try:
        response = client.open(request, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def main():
    status, headers, data = call("/ready")
    assert status == 200 and json.loads(data) == {"ready": True}, "Readiness publico invalido"
    assert "uvicorn" not in headers.get("Server", "").lower(), "Cabecera Uvicorn expuesta"
    for route in ("/api/docs", "/api/redoc", "/api/openapi.json", "/docs", "/openapi.json"):
        status, _headers, body = call(route)
        # Una ruta SPA puede responder HTML; nunca debe servir Swagger/OpenAPI.
        if route.startswith("/api/"):
            assert status == 404, route
        else:
            assert status == 404 or (status == 200 and b"swagger-ui" not in body and b'"openapi"' not in body), route
    assert call("/ready/details")[0] == 401, "Detalle de readiness sin sesion"
    status, _, html = call("/")
    assert status == 200 and b'<div id="root"' in html, "Frontend distribuido ausente"
    password = (ROOT / "backend/config/secrets/docker/bootstrap-admin-password.txt").read_text().strip()
    status, headers, data = call("/api/v1/auth/local/login", method="POST",
                                payload={"username": "Matrix", "password": password})
    assert status == 200, "Login local con MySQL no disponible"
    cookie_header = headers.get("Set-Cookie", "").lower()
    assert "httponly" in cookie_header and "secure" in cookie_header and "samesite=lax" in cookie_header
    csrf = json.loads(data)["csrf_token"]
    assert call("/ready/details")[0] == 200, "Administrador sin diagnosticos"
    status, _, data = call("/api/v1/conversations", method="POST", payload={"title": "Synthetic Docker smoke"}, csrf=csrf)
    assert status in (200, 201), "Creacion de conversacion no disponible"
    conversation_id = json.loads(data)["id"]
    assert call("/api/v1/conversations/" + conversation_id)[0] == 200, "Conversacion no visible tras respuesta"
    print(json.dumps({"status": "PASS", "scope": "clean_docker_build_prepare_https_login",
                      "database": "real_mysql", "vector_store": "real_qdrant",
                      "model": "synthetic_http_fixture", "real_model_quality": "NOT_EVALUATED"}))


if __name__ == "__main__":
    main()
