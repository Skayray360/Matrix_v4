# Creado por Aldo Garcia.
"""Geometria coseno verificada antes de publicar un indice o usar un embedding."""

from __future__ import annotations

import httpx
import pytest
from qdrant_client import QdrantClient, models

from app.common.errors import EmbeddingDimensionMismatchError, QdrantUnavailableError
from app.config.settings import Settings
from app.llm.ollama_client import OllamaClient
from app.llm.provider import ModelClient
from app.rag.vector_store import VectorStore
from tests.unit.test_rag_pipeline_isolated import DIMENSION, make_chunk

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("distance", [models.Distance.DOT, models.Distance.EUCLID, models.Distance.MANHATTAN])
def test_same_dimension_incompatible_metric_is_rejected_without_recreation(distance):
    client = QdrantClient(location=":memory:")
    store = VectorStore(client=client)
    collection = store.collection_for("corporate")
    client.create_collection(collection, vectors_config=models.VectorParams(size=DIMENSION, distance=distance))
    try:
        with pytest.raises(QdrantUnavailableError):
            store.ensure_collection(collection)
        assert client.get_collection(collection).config.params.vectors.distance == distance
        assert collection not in store._ensured
    finally:
        store.close()


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
@pytest.mark.parametrize("component", [0.0, 1e-100, 1e100, 1e-30, 1e30, 10 ** 400])
def test_runtime_unrepresentable_cosine_embeddings_are_rejected(provider, component, monkeypatch):
    settings = Settings(_env_file=None, llm_embedding_provider=provider)
    payload = ({"embeddings": [[component] * DIMENSION]} if provider == "ollama" else
               {"data": [{"index": 0, "embedding": [component] * DIMENSION}]})
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    with httpx.Client(transport=transport) as client:
        runtime = (OllamaClient(client=client, settings=settings) if provider == "ollama" else
                   ModelClient(client=client, settings=settings))
        if provider != "ollama":
            monkeypatch.setattr(runtime, "embedding_revision", lambda: "synthetic-revision")
        with pytest.raises(EmbeddingDimensionMismatchError):
            runtime.embed_one("consulta sintetica")
        assert not runtime._embedding_cache


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
def test_valid_embeddings_are_cached_without_rescaling_or_mutation(provider, monkeypatch):
    settings = Settings(_env_file=None, llm_embedding_provider=provider)
    vector = [1e-100, -0.123456789] + [0.1] * (DIMENSION - 2)
    payload = ({"embeddings": [vector]} if provider == "ollama" else
               {"data": [{"index": 0, "embedding": vector}]})
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        runtime = (OllamaClient(client=client, settings=settings) if provider == "ollama" else
                   ModelClient(client=client, settings=settings))
        if provider != "ollama":
            monkeypatch.setattr(runtime, "embedding_revision", lambda: "synthetic-revision")
        assert runtime.embed_one("consulta sintetica") == vector
        assert runtime.embed_one("consulta sintetica") == vector
        assert len(calls) == 1


@pytest.mark.parametrize("invalid", [
    [0.0] * DIMENSION,
    [float("nan")] + [0.1] * (DIMENSION - 1),
    [float("inf")] + [0.1] * (DIMENSION - 1),
    [True] + [0.1] * (DIMENSION - 1),
    [1e-100] * DIMENSION,
    [1e100] * DIMENSION,
    [1e-30] * DIMENSION,
    [1e30] * DIMENSION,
    [10 ** 400] + [0.1] * (DIMENSION - 1),
])
def test_invalid_late_vector_rejects_entire_batch_before_any_write(invalid):
    client = QdrantClient(location=":memory:")
    store = VectorStore(client=client)
    chunks = [make_chunk("dato sintetico", category="prestaciones", index=index) for index in range(65)]
    vectors = [[0.1] * DIMENSION for _ in chunks]
    vectors[-1] = invalid
    try:
        with pytest.raises(QdrantUnavailableError):
            store.upsert_chunks(chunks, vectors)
        assert client.get_collections().collections == []
    finally:
        store.close()
