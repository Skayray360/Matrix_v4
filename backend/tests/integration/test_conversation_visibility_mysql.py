# Creado por Aldo Garcia.
"""Visibilidad al responder el alta con MySQL descartable y REPEATABLE READ."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.api import deps
from app.api.middleware import matrix_error_handler
from app.api.routes import conversations
from app.common.errors import MatrixError
from app.database.models import Conversation, User
from tests.conftest import make_context
from tests.integration.test_generation_recovery_mysql import mysql_engine
from tests.unit.test_conversation_creation_visibility import observe_immediate_attachment

pytestmark = pytest.mark.integration
__all__ = ["mysql_engine"]


@pytest.fixture
def conversation_mysql(mysql_engine, monkeypatch):
    factory = sessionmaker(mysql_engine, expire_on_commit=False)
    ctx = make_context()
    with factory.begin() as db:
        db.add(User(id=ctx.user_id, username="visibility-" + ctx.user_id,
                    display_name="Synthetic visibility owner", auth_source="local_test"))
    monkeypatch.setattr(deps, "get_sessionmaker", lambda: factory)
    app = FastAPI()
    app.include_router(conversations.router, prefix="/api/v1")
    app.add_exception_handler(MatrixError, matrix_error_handler)
    app.dependency_overrides[deps.get_user_context] = lambda: ctx
    app.dependency_overrides[deps.require_csrf] = lambda: None
    try:
        yield SimpleNamespace(app=app, factory=factory, ctx=ctx)
    finally:
        # Solo el usuario UUID de este caso y sus conversaciones. El adjunto
        # invalido no alcanza extraccion, Qdrant ni publicacion documental.
        with factory.begin() as db:
            db.execute(delete(Conversation).where(Conversation.user_id == ctx.user_id))
            db.execute(delete(User).where(User.id == ctx.user_id))


@pytest.mark.parametrize("owner", [True, False])
def test_mysql_create_is_visible_to_immediate_attachment_before_teardown(conversation_mysql, owner):
    observed = observe_immediate_attachment(conversation_mysql, owner=owner)
    assert observed == {"status": 200, "visible_at_headers": 1,
                        "attachment_status": 415 if owner else 404}
