# Creado por Aldo Garcia.
"""Regresiones de localidad y pins con transportes sinteticos, sin ADC ni red."""

import builtins

import httpx
import pytest

from app.common.errors import ConfigurationError
from app.config.settings import Settings
from app.llm.provider import ModelClient

pytestmark = pytest.mark.unit


def test_legacy_vertex_endpoint_never_loads_credentials_for_local_inference(monkeypatch):
    settings = Settings(
        _env_file=None, ollama_embedding_dimension=1, rag_embedding_dimension=1,
        llm_vertex_base_url="http://127.0.0.1:11434",
    )
    original_import = builtins.__import__

    def deny_cloud_import(name, *args, **kwargs):
        if name.startswith("google.auth"):
            pytest.fail("Una solicitud local intento cargar credenciales cloud")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_cloud_import)
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"embeddings": [[1.0]]}),
    )) as http:
        assert ModelClient(settings=settings, client=http).embed(["sintetico"]) == [[1.0]]


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
def test_embedding_cloud_override_is_rejected_before_network(provider):
    settings = Settings(_env_file=None, llm_embedding_provider=provider)
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("No debe abrir red"))) as http,
        pytest.raises(ConfigurationError, match="cloud"),
    ):
        ModelClient(settings=settings, client=http).embed(["sintetico"], model="synthetic:cloud")


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
def test_embedding_pin_is_checked_before_each_batch_and_cached_query(provider):
    settings = Settings(
        _env_file=None, llm_embedding_provider=provider,
        llm_embedding_digest="a" * 64, llm_embedding_revision="revision-local",
        ollama_embedding_dimension=1, rag_embedding_dimension=1,
    )
    observed = ["a" * 64]
    posts = []

    def transport(request):
        if request.method == "GET":
            entry = {"name": settings.ollama_embedding_model, "id": settings.ollama_embedding_model,
                     "digest": observed[0]}
            return httpx.Response(200, json={"models": [entry], "data": [entry]})
        posts.append(request.url.path)
        return httpx.Response(200, json={"embeddings": [[1.0]], "data": [{"index": 0, "embedding": [1.0]}]})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = ModelClient(settings=settings, client=http)
        assert client.embed_one("sintetico") == [1.0]
        observed[0] = "b" * 64
        # Tanto una nueva ingesta como una consulta cacheada deben detectar
        # pesos sustituidos durante la vida del proceso.
        with pytest.raises(ConfigurationError, match="digest"):
            client.embed(["otro documento sintetico"])
        with pytest.raises(ConfigurationError, match="digest"):
            client.embed_one("sintetico")
    assert len(posts) == 1


def test_query_vector_from_changed_revision_never_enters_cache():
    settings = Settings(_env_file=None, ollama_embedding_dimension=1, rag_embedding_dimension=1)
    observed = ["a" * 64]

    def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [
                {"name": settings.ollama_embedding_model, "digest": observed[0]},
            ]})
        # Simula una sustitucion de pesos mientras el lote esta en curso.
        observed[0] = "b" * 64
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = ModelClient(settings=settings, client=http)
        with pytest.raises(ConfigurationError, match="cambio"):
            client.embed_one("consulta sintetica")
        assert not client._embedding_cache
