# Creado por Aldo Garcia.
"""Limites de recepcion antes de cualquier parser, tambien sin proxy."""

from __future__ import annotations

import asyncio
import tempfile
import time
from contextlib import ExitStack

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.common.errors import MatrixError
from app.config import get_settings
from app.security.admission import admission


class _BodyRejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message


def _declared_length(headers: list[tuple[bytes, bytes]], limit: int) -> int | None:
    lengths = [value for key, value in headers if key.lower() == b"content-length"]
    encodings = [value for key, value in headers if key.lower() == b"transfer-encoding"]
    if len(lengths) > 1 or (lengths and encodings):
        raise _BodyRejected(400, "El encuadre de la solicitud es ambiguo.")
    if not lengths:
        return None
    value = lengths[0]
    if not value.isdigit() or len(value) > 20:
        raise _BodyRejected(400, "Content-Length no es valido.")
    length = int(value)
    if length > limit:
        raise _BodyRejected(413, "La solicitud excede el limite permitido.")
    return length


async def _read_body(
    receive: Receive, body: tempfile.SpooledTemporaryFile[bytes], *, limit: int, timeout: float,
) -> int | None:
    total = 0
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        message = await asyncio.wait_for(receive(), timeout=remaining)
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            raise _BodyRejected(413, "La solicitud excede el limite permitido.")
        body.write(chunk)
        if not message.get("more_body", False):
            return total


class UploadBodyLimitMiddleware:
    """Conserva el nombre publico; limita JSON, texto, binario y multipart."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = get_settings()
        headers = scope.get("headers", [])
        content_types = [value for key, value in headers if key.lower() == b"content-type"]
        multipart = bool(content_types and content_types[0].split(b";", 1)[0].strip().lower()
                         == b"multipart/form-data")
        limit = settings.upload_body_max_bytes if multipart else settings.http_body_max_bytes
        timeout = settings.extraction_timeout_seconds if multipart else settings.http_body_timeout_seconds
        capacity = settings.upload_max_inflight if multipart else settings.http_max_inflight
        # El buffer se cierra tambien si la conexion se cancela o el parser falla.
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body, ExitStack() as controls:
            try:
                if len(content_types) > 1:
                    raise _BodyRejected(400, "Content-Type es ambiguo.")
                declared = _declared_length(headers, limit)
                controls.enter_context(admission("upload" if multipart else "http_body", capacity))
                total = await _read_body(receive, body, limit=limit, timeout=timeout)
                if not multipart:
                    controls.close()
                if total is None:
                    return
                if declared is not None and declared != total:
                    raise _BodyRejected(400, "El cuerpo no coincide con Content-Length.")
            except TimeoutError:
                return await self._reject(scope, receive, send, 408, "La solicitud excedio el tiempo permitido.")
            except _BodyRejected as exc:
                return await self._reject(scope, receive, send, exc.status, exc.message)
            except MatrixError as exc:
                return await JSONResponse(exc.to_public_dict(), status_code=exc.status_code,
                                          headers={"Retry-After": "5"})(scope, receive, send)
            body.seek(0)
            exhausted = False

            async def replay():
                nonlocal exhausted
                if exhausted:
                    return await receive()
                chunk = body.read(64 * 1024)
                more = body.tell() < total
                exhausted = not more
                return {"type": "http.request", "body": chunk, "more_body": more}

            # Errores del endpoint corresponden a sus handlers, no a recepcion.
            await self.app(scope, replay, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, message: str) -> None:
        await JSONResponse({"code": "file_too_large" if status == 413 else "validation_error", "message": message},
                           status_code=status)(scope, receive, send)
