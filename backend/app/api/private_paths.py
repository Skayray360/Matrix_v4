# Creado por Aldo Garcia.
"""Deniega segmentos ocultos antes de los estaticos y del fallback de la SPA."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.common.errors import ErrorCode


def is_private_path(path: str) -> bool:
    """Inspecciona el path ya decodificado por ASGI, sin incluir la query.

    La barra inversa tambien es un separador en el despliegue Windows. Los
    puntos dentro de un nombre publico (por ejemplo, ``app.hash.js``) son validos.
    """
    return any(segment.startswith(".") for segment in path.replace("\\", "/").split("/"))


class PrivatePathsMiddleware:
    """No permite que un dotfile copiado al build se vuelva un recurso publico."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_private_path(scope["path"]):
            await JSONResponse(
                status_code=404,
                content={"code": str(ErrorCode.NOT_FOUND), "message": "Recurso no encontrado."},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
