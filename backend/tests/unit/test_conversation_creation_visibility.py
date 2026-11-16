# Creado por Aldo Garcia.
"""La respuesta de alta no debe adelantarse a su commit SQL.

Dos conexiones SQLite y el send ASGI reproducen la ventana entre la entrega de
la respuesta y el teardown de Depends. Solo se sustituyen identidad/CSRF; la
creacion, la propiedad, la validacion de adjuntos y la transaccion son reales.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps
from app.api.middleware import matrix_error_handler
from app.api.routes import conversations
from app.common.errors import MatrixError
from app.database.models import Base, Conversation, User
from tests.conftest import make_context


@pytest.fixture
def conversation_http(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "conversations.sqlite"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    ctx = make_context()
    with factory.begin() as db:
        db.add(User(id=ctx.user_id, username="SyntheticOwner", display_name="Synthetic owner", auth_source="local_test"))
    monkeypatch.setattr(deps, "get_sessionmaker", lambda: factory)
    app = FastAPI()
    app.include_router(conversations.router, prefix="/api/v1")
    app.add_exception_handler(MatrixError, matrix_error_handler)
    app.dependency_overrides[deps.get_user_context] = lambda: ctx
    app.dependency_overrides[deps.require_csrf] = lambda: None
    yield SimpleNamespace(app=app, factory=factory, ctx=ctx, engine=engine)
    engine.dispose()


def observe_immediate_attachment(env, *, owner):
    """Escenario compartido con MySQL; solo examina el usuario sintetico propio."""
    observed = {}

    async def run():
        payload = json.dumps({"title": "Synthetic immediate attachment"}).encode()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "POST", "scheme": "http", "path": "/api/v1/conversations",
                 "raw_path": b"/api/v1/conversations", "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"application/json"),
                             (b"content-length", str(len(payload)).encode())],
                 "client": ("127.0.0.1", 1234), "server": ("testserver", 80)}

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                observed["status"] = message["status"]
                with env.factory() as observer:
                    observed["visible_at_headers"] = len(observer.scalars(
                        select(Conversation).where(Conversation.user_id == env.ctx.user_id),
                    ).all())
            elif message["type"] == "http.response.body":
                conversation_id = json.loads(message["body"])["id"]
                if not owner:
                    env.app.dependency_overrides[deps.get_user_context] = lambda: make_context()
                # El archivo invalido termina antes de Qdrant/Ollama. Un 415
                # acredita que el dueno supero la busqueda; un ajeno conserva 404.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                             base_url="http://testserver") as client:
                    response = await client.post(f"/api/v1/conversations/{conversation_id}/attachments",
                                                 files={"files": ("synthetic.exe", b"MZ", "application/octet-stream")})
                observed["attachment_status"] = response.status_code

        await env.app(scope, receive, send)

    asyncio.run(run())
    return observed


@pytest.mark.parametrize("owner", [True, False])
def test_next_attachment_checks_committed_conversation_before_create_teardown(conversation_http, owner):
    observed = observe_immediate_attachment(conversation_http, owner=owner)
    assert observed == {"status": 200, "visible_at_headers": 1,
                        "attachment_status": 415 if owner else 404}


def test_create_commit_failure_returns_no_success_and_rolls_back(conversation_http, monkeypatch):
    env = conversation_http

    class FailingCommitSession(Session):
        def commit(self):
            raise RuntimeError("Synthetic commit failure")

    failing_factory = sessionmaker(env.engine, class_=FailingCommitSession, expire_on_commit=False)
    monkeypatch.setattr(deps, "get_sessionmaker", lambda: failing_factory)
    with TestClient(env.app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/conversations", json={"title": "Synthetic uncommitted"})
    assert response.status_code == 500
    with env.factory() as observer:
        assert observer.scalars(select(Conversation)).all() == []
