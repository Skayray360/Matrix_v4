# Creado por Aldo Garcia.
"""Cupos y documentos sinteticos: reproduce los hallazgos M3/B2 sin servicios."""

import asyncio
import json
import threading
from contextlib import suppress

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import body_limit
from app.api.deps import get_db, get_user_context, require_csrf
from app.common.errors import ExtractionFailedError
from app.config import get_settings
from app.ingestion.isolated_extraction import extract_document as isolated_extract
from app.ingestion.loaders import extract_document
from app.main import create_app
from app.security.admission import admission, snapshot
from tests.unit import test_attachment_preparation_transactions as preparation_fixtures

pytestmark = pytest.mark.unit
environment = preparation_fixtures.environment
upload_state = preparation_fixtures.upload_state


def scope(request_id=None):
    return {
        "type": "http", "method": "POST", "headers": [(b"content-type", b"multipart/form-data")],
        "state": {"request_id": request_id} if request_id else {},
    }


async def body():
    return {"type": "http.request", "body": b"synthetic", "more_body": False}


@pytest.mark.asyncio
async def test_six_back_to_back_uploads_wait_for_previous_request_cleanup(monkeypatch):
    settings = get_settings().model_copy(update={"upload_max_inflight": 1})
    monkeypatch.setattr(body_limit, "get_settings", lambda: settings)
    cleanup = [asyncio.Event() for _ in range(6)]
    response_sent = [asyncio.Event() for _ in range(6)]
    statuses = [None] * 6
    active = 0
    peak = 0

    async def app(request_scope, receive, send):
        nonlocal active, peak
        index = int(request_scope["state"]["request_id"])
        active += 1
        peak = max(peak, active)
        try:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})
            # Una respuesta ya recibida no significa que el request haya acabado.
            assert snapshot()["upload"] == 1
            await cleanup[index].wait()
        finally:
            active -= 1

    middleware = body_limit.UploadBodyLimitMiddleware(app)

    async def request(index):
        async def send(message):
            if message["type"] == "http.response.start":
                statuses[index] = message["status"]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_sent[index].set()

        await middleware(scope(str(index)), body, send)

    tasks = []
    try:
        for index in range(6):
            tasks.append(asyncio.create_task(request(index)))
            if index:
                asyncio.get_running_loop().call_later(0.02, cleanup[index - 1].set)
            await asyncio.wait_for(response_sent[index].wait(), timeout=2)
        assert statuses == [200] * 6
        assert peak == 1
    finally:
        for event in cleanup:
            event.set()
        await asyncio.gather(*tasks)
    assert not snapshot().get("upload") and not snapshot().get("upload_wait")


@pytest.mark.parametrize("occupied", ["upload", "upload_wait"])
@pytest.mark.asyncio
async def test_real_capacity_exhaustion_is_bounded_typed_and_does_not_consume_body(monkeypatch, occupied):
    settings = get_settings().model_copy(update={"upload_max_inflight": 1, "http_max_inflight": 1})
    monkeypatch.setattr(body_limit, "get_settings", lambda: settings)
    # Se mantiene el algoritmo real con un presupuesto menor para la regresion.
    monkeypatch.setattr(body_limit, "UPLOAD_ADMISSION_WAIT_SECONDS", 0.02)
    sent = []

    async def forbidden(*args):
        pytest.fail("La capacidad ocupada no debe consumir el cuerpo ni entrar al parser")

    async def send(message):
        sent.append(message)

    with admission(occupied, 1):
        await asyncio.wait_for(body_limit.UploadBodyLimitMiddleware(forbidden)(
            scope("synthetic-request"), forbidden, send,
        ), timeout=1)
        assert snapshot()[occupied] == 1
    assert sent[0]["status"] == 503
    payload = json.loads(sent[1]["body"])
    assert payload["code"] == "upload_capacity_full"
    assert payload["request_id"] == "synthetic-request"
    headers = dict(sent[0]["headers"])
    assert headers[b"x-request-id"] == b"synthetic-request" and headers[b"retry-after"] == b"1"
    assert not snapshot().get("upload") and not snapshot().get("upload_wait")


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_release_active_upload_or_leak_waiter(monkeypatch):
    settings = get_settings().model_copy(update={"upload_max_inflight": 1})
    monkeypatch.setattr(body_limit, "get_settings", lambda: settings)

    async def forbidden(*args):
        pytest.fail("Una solicitud en espera no debe recibir cuerpo ni alcanzar endpoint")

    with admission("upload", 1):
        task = asyncio.create_task(body_limit.UploadBodyLimitMiddleware(forbidden)(scope(), forbidden, forbidden))
        try:
            for _ in range(50):
                if snapshot().get("upload_wait"):
                    break
                await asyncio.sleep(0)
            assert snapshot().get("upload_wait") == 1
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        assert snapshot().get("upload") == 1 and not snapshot().get("upload_wait")
    assert not snapshot().get("upload")


@pytest.mark.parametrize("failure", ["disconnect", "endpoint_error"])
@pytest.mark.asyncio
async def test_upload_slot_is_released_on_disconnect_or_endpoint_error(failure):
    async def receive():
        return {"type": "http.disconnect"} if failure == "disconnect" else await body()

    async def app(*args):
        if failure == "disconnect":
            pytest.fail("Un cliente desconectado no debe entrar al endpoint")
        raise RuntimeError("synthetic endpoint error")

    async def send(message):
        pytest.fail("No se produce una respuesta desde este endpoint sintetico")

    middleware = body_limit.UploadBodyLimitMiddleware(app)
    if failure == "endpoint_error":
        with pytest.raises(RuntimeError, match="synthetic"):
            await middleware(scope(), receive, send)
    else:
        await middleware(scope(), receive, send)
    assert not snapshot().get("upload") and not snapshot().get("upload_wait")


def test_capacity_response_reuses_server_request_id_through_http_middlewares(monkeypatch):
    monkeypatch.setattr(body_limit, "get_settings", lambda: get_settings().model_copy(update={"upload_max_inflight": 1}))
    monkeypatch.setattr(body_limit, "UPLOAD_ADMISSION_WAIT_SECONDS", 0.01)
    with admission("upload", 1):
        response = TestClient(create_app()).post(
            "/api/v1/conversations/synthetic/attachments", content=b"synthetic",
            headers={"Content-Type": "multipart/form-data", "X-Request-ID": "untrusted-client-id"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "upload_capacity_full"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert response.json()["request_id"] != "untrusted-client-id"


@pytest.mark.asyncio
async def test_cancelling_http_request_preserves_slot_until_sync_worker_finishes():
    app = create_app()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    @app.post("/__synthetic_upload_worker")
    def worker():
        entered.set()
        assert release.wait(timeout=2), "El fixture debe liberar su worker sintetico"
        finished.set()
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        task = asyncio.create_task(client.post(
            "/__synthetic_upload_worker", content=b"synthetic", headers={"Content-Type": "multipart/form-data"},
        ))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.03)
            assert not finished.is_set() and not task.done()
            assert snapshot().get("upload") == 1
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await task
    assert finished.is_set() and not snapshot().get("upload")


@pytest.mark.parametrize("filename", ["synthetic.txt", "synthetic.md", "synthetic.csv"])
@pytest.mark.parametrize("data", [b"\x00" * 20000, b"text\x01\x02\x03", "text\u009f".encode()])
def test_binary_control_content_is_rejected_as_document_error(filename, data):
    with pytest.raises(ExtractionFailedError, match="binarios") as rejected:
        extract_document(data, filename=filename)
    assert rejected.value.status_code == 422


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1252", "latin-1"])
def test_legitimate_windows_text_encodings_and_whitespace_remain_accepted(encoding):
    text = "Nómina\tAntigüedad\nPolítica\r\n\fSección\vFinal"
    result = extract_document(text.encode(encoding), filename="synthetic.txt")
    assert "Nómina" in result.plain_text() and "Antigüedad" in result.plain_text()


def test_isolated_parser_preserves_typed_binary_rejection():
    with pytest.raises(ExtractionFailedError, match="binarios") as rejected:
        isolated_extract(b"\x00" * 20000, filename="synthetic.txt")
    assert rejected.value.status_code == 422


@pytest.mark.parametrize("data", [b"\x00" * 20000, b"x" * 20000])
def test_invalid_attachment_returns_http_422_without_embedding_or_publication(upload_state, monkeypatch, data):
    env = upload_state

    def embedding_forbidden(*args, **kwargs):
        pytest.fail("El contenido rechazado no debe alcanzar embeddings")

    monkeypatch.setattr(env.llm, "embed", embedding_forbidden)
    app = create_app()

    def database():
        try:
            yield env.db
            env.db.commit()
        except Exception:
            env.db.rollback()
            raise

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_user_context] = lambda: env.context
    app.dependency_overrides[require_csrf] = lambda: None
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/conversations/chat-a/attachments", files={"files": ("synthetic.txt", data, "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "extraction_failed"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    preparation_fixtures.assert_not_published(env)


def test_corporate_indivisible_text_is_typed_without_embedding(upload_state, monkeypatch):
    env = upload_state
    source = env.settings.knowledge_root_path / "synthetic.txt"
    source.write_bytes(b"x" * 20000)
    monkeypatch.setattr(env.llm, "embed", lambda *args, **kwargs: pytest.fail("No debe embeber contenido rechazado"))
    with pytest.raises(ExtractionFailedError, match="unidad demasiado grande"):
        env.service.ingest_corporate_file(env.db, absolute_path=source, relative_path="general/synthetic.txt",
                                          category="general")
    preparation_fixtures.assert_not_published(env)
