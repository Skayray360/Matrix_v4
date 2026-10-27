# Creado por Aldo Garcia.
"""Frontera HTTP: autoridad local/configurada y procedencia de las mutaciones."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import AppEnv, Settings


def _authority(value: str) -> tuple[str, int | None]:
    if not value or any(char in value for char in "/\\?#@") or any(ord(char) <= 32 for char in value):
        raise ValueError("Autoridad HTTP no valida")
    parsed = urlsplit("//" + value)
    hostname = parsed.hostname or ""
    if not hostname or value.endswith(":"):
        raise ValueError("Autoridad HTTP incompleta")
    return hostname.casefold().rstrip("."), parsed.port


def _origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Origen HTTP no valido")
    hostname, port = _authority(parsed.netloc)
    return parsed.scheme, hostname, port or (443 if parsed.scheme == "https" else 80)


def _allowed_hosts(settings: Settings) -> frozenset[str]:
    hosts = {"127.0.0.1", "localhost", "::1", urlsplit(settings.app_base_url).hostname or ""}
    try:
        wildcard = ip_address(settings.app_host).is_unspecified
    except ValueError:
        wildcard = False
    if not wildcard:
        hosts.add(settings.app_host)
    if settings.app_env is AppEnv.TEST:
        hosts.add("testserver")
    return frozenset(host.casefold().rstrip(".") for host in hosts if host)


class RequestBoundaryMiddleware:
    """No confia en X-Forwarded-Host ni convierte CORS en autorizacion."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.hosts = _allowed_hosts(settings)
        self.public_origin = _origin(settings.app_base_url)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        if sum(len(key) + len(value) + 4 for key, value in headers) > 16_384:
            return await self._reject(scope, receive, send, 431, "Las cabeceras exceden el limite permitido.")
        hosts = [value.decode("latin-1") for key, value in headers if key.lower() == b"host"]
        try:
            if len(hosts) != 1 or _authority(hosts[0])[0] not in self.hosts:
                raise ValueError("Host no autorizado")
        except ValueError:
            return await self._reject(scope, receive, send, 400, "La autoridad de la solicitud no es valida.")
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origins = [value.decode("latin-1") for key, value in headers if key.lower() == b"origin"]
            fetch_sites = [value for key, value in headers if key.lower() == b"sec-fetch-site"]
            try:
                request_origin = _origin(f"{scope.get('scheme', 'http')}://{hosts[0]}")
                if (len(origins) > 1 or b"cross-site" in fetch_sites
                        or (origins and _origin(origins[0]) not in {self.public_origin, request_origin})):
                    raise ValueError("Origen no autorizado")
            except ValueError:
                return await self._reject(scope, receive, send, 403, "El origen de la solicitud no esta permitido.")
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, message: str) -> None:
        await JSONResponse({"code": "forbidden" if status == 403 else "validation_error", "message": message},
                           status_code=status)(scope, receive, send)
