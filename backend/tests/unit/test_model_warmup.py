# Creado por Aldo Garcia.
"""Precalentamiento con HTTP simulado: sin pesos, corpus ni servicios reales."""

import json
from threading import Event

import httpx
import pytest

from app.config import Settings
from app.llm.provider import ModelClient
from app.llm.request_control import inference_control, remaining_inference_seconds
from scripts import model_warmup as warmup

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings(monkeypatch):
    configured = Settings(
        _env_file=None, ollama_fast_model="fixture-chat:latest", ollama_deep_model="fixture-chat:latest",
        ollama_embedding_model="fixture-embed:latest", ollama_embedding_dimension=3, rag_embedding_dimension=3,
        llm_fast_digest="a" * 64, llm_deep_digest="a" * 64, llm_embedding_digest="b" * 64,
        llm_system_prefix="private-system-prefix", ollama_keep_alive="15m", llm_completion_retries=1,
    )
    monkeypatch.setattr(warmup, "get_settings", lambda: configured)
    monkeypatch.setattr("app.llm.provider.get_settings", lambda: configured)
    return configured


def install_transport(monkeypatch, settings, *, handler=None):
    requests = []
    clients = []

    def respond(request):
        requests.append(request)
        if handler is not None:
            intercepted = handler(request)
            if intercepted is not None:
                return intercepted
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": settings.ollama_fast_model, "digest": "sha256:" + settings.llm_fast_digest},
                {"name": settings.ollama_embedding_model, "digest": "sha256:" + settings.llm_embedding_digest},
            ]})
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.25, 0.5, 1.0]]})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={
                "message": {"content": "private-generated-response"}, "done": True, "done_reason": "stop",
                "prompt_eval_count": 19, "eval_count": 2, "load_duration": 4_000_000,
            })
        pytest.fail("Unexpected endpoint")

    def create(**kwargs):
        client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        clients.append(client)
        return ModelClient(client=client, **kwargs)

    monkeypatch.setattr(warmup, "ModelClient", create)
    return requests, clients


def test_default_is_plan_without_client_creation(settings, monkeypatch, capsys):
    monkeypatch.setattr(warmup, "ModelClient", lambda **_: pytest.fail("Inference was not authorized"))
    assert warmup.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "planned" and not report["executed"]
    assert report["probes"] == [] and report["roles"] == ["embedding", "generation"]
    assert "private" not in json.dumps(report)


def test_run_uses_pinned_models_once_with_configured_context_and_keepalive(settings, monkeypatch, capsys):
    requests, clients = install_transport(monkeypatch, settings)
    assert warmup.main(["--run", "--profile", "deep", "--timeout-seconds", "30"]) == 0
    report = json.loads(capsys.readouterr().out)
    posts = [request for request in requests if request.method == "POST"]
    assert [request.url.path for request in posts] == ["/api/embed", "/api/chat"]
    assert requests[0].url.path == "/api/tags"
    assert all(request.url.path in {"/api/tags", "/api/embed", "/api/chat"} for request in requests)
    embedding, chat = [json.loads(request.content) for request in posts]
    assert embedding["input"] == [warmup.EMBEDDING_PROBE]
    assert embedding["model"] == settings.ollama_embedding_model
    assert embedding["truncate"] is False
    assert chat["model"] == settings.ollama_deep_model and chat["think"] is False and chat["stream"] is False
    assert chat["options"]["num_ctx"] == settings.ollama_deep_num_ctx
    assert chat["options"]["num_predict"] == 32 and chat["options"]["temperature"] == 0
    assert chat["messages"][-1] == {"role": "user", "content": warmup.CHAT_PROBE}
    assert embedding["keep_alive"] == chat["keep_alive"] == settings.ollama_keep_alive
    assert settings.llm_completion_retries == 1
    assert report["status"] == "completed" and report["pins_configured"]
    assert report["probes"][0]["dimension"] == 3
    assert report["probes"][1]["load_duration_ns"] == 4_000_000
    serialized = json.dumps(report)
    assert all(value not in serialized for value in (
        "private", warmup.CHAT_PROBE, warmup.EMBEDDING_PROBE, "0.25", settings.ollama_fast_model,
    ))
    assert report["resident_models_verified"] is False and report["corporate_rag_validated"] is False
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("field,value,error", [
    ("llm_fast_digest", "", "model_pins_required"),
    ("llm_provider", "openai_compatible", "local_ollama_required"),
    ("llm_local_only", False, "local_ollama_required"),
    ("ollama_base_url", "http://unapproved.invalid:11434", "configuration_or_model_pin_rejected"),
])
def test_invalid_configuration_never_reaches_http(settings, monkeypatch, field, value, error):
    settings = settings.model_copy(update={field: value})
    monkeypatch.setattr(warmup, "get_settings", lambda: settings)
    requests, _ = install_transport(monkeypatch, settings)
    report = warmup.run_warmup(execute=True, target="generation")
    assert report["errors"] == [error] and report["status"] == "blocked"
    assert requests == []


@pytest.mark.parametrize("inventory", [[], [{"name": "fixture-chat:latest", "digest": "c" * 64}]])
def test_missing_or_changed_model_is_blocked_before_post(settings, monkeypatch, inventory):
    def handler(request):
        return httpx.Response(200, json={"models": inventory})

    requests, clients = install_transport(monkeypatch, settings, handler=handler)
    report = warmup.run_warmup(execute=True)
    assert report["errors"] == ["configuration_or_model_pin_rejected"]
    assert report["status"] == "blocked" and report["probes"] == []
    assert all(request.method == "GET" for request in requests)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("failure,expected", [(httpx.ConnectError, "transport"), (httpx.ReadTimeout, "timeout")])
def test_unavailable_runtime_returns_sanitized_error_without_retry(settings, monkeypatch, failure, expected):
    def handler(request):
        raise failure("private at https://secret:password@host", request=request)

    requests, clients = install_transport(monkeypatch, settings, handler=handler)
    report = warmup.run_warmup(execute=True)
    assert report["status"] == "blocked" and report["errors"] == [expected]
    assert len(requests) == 1 and requests[0].method == "GET"
    assert "private" not in json.dumps(report) and "password" not in json.dumps(report)
    assert all(client.is_closed for client in clients)


def test_incomplete_probe_is_not_regenerated(settings, monkeypatch):
    def handler(request):
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={
                "message": {"content": "private incomplete"}, "done": True, "done_reason": "length",
            })
        return None

    requests, _ = install_transport(monkeypatch, settings, handler=handler)
    report = warmup.run_warmup(execute=True, target="generation")
    assert report["status"] == "failed" and report["errors"] == ["incomplete"]
    assert report["probes"][0]["status"] == "failed"
    assert len([request for request in requests if request.method == "POST"]) == 1


def test_invalid_embedding_stops_before_generation(settings, monkeypatch):
    def handler(request):
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0, 0, 0]]})
        return None

    requests, _ = install_transport(monkeypatch, settings, handler=handler)
    report = warmup.run_warmup(execute=True)
    assert report["status"] == "failed" and report["errors"] == ["invalid_embedding"]
    assert not any(request.url.path == "/api/chat" for request in requests)


def test_nested_deadline_is_preserved_and_http_is_bounded(settings, monkeypatch):
    requests, _ = install_transport(monkeypatch, settings)
    with inference_control(2):
        before = remaining_inference_seconds()
        report = warmup.run_warmup(execute=True, timeout_seconds=30)
        assert 0 < remaining_inference_seconds() <= before
    assert report["status"] == "completed"
    assert all(0 < request.extensions["timeout"]["read"] <= 2 for request in requests)
    assert remaining_inference_seconds() is None


def test_parent_cancellation_stops_before_inventory(settings, monkeypatch):
    requests, _ = install_transport(monkeypatch, settings)
    cancelled = Event()
    with inference_control(60, cancel_event=cancelled):
        cancelled.set()
        report = warmup.run_warmup(execute=True)
    assert report["errors"] == ["cancelled"] and requests == []


@pytest.mark.parametrize("timeout", ["0", "301", "nan", "inf"])
def test_invalid_timeout_rejected_before_execution(timeout):
    with pytest.raises(SystemExit) as exc:
        warmup.main(["--run", "--timeout-seconds", timeout])
    assert exc.value.code == 2
