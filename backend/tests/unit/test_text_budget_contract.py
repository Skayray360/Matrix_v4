# Creado por Aldo Garcia.
"""Limites sinteticos multilingues y estructurales, sin tokenizer ni inferencia."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from app.agents.knowledge_agent import KnowledgeAgent
from app.agents.query_planner import build_query_plan
from app.common.inference_errors import InferenceFailureError, InferenceFailureKind
from app.common.text_budget import TextBudget, estimate_tokens
from app.config import get_settings
from app.llm.model_policy import Intent, ModelPolicy
from app.memory.service import ConversationContext
from app.rag import index_manifest
from app.rag.chunking import ChunkBudgetExceededError, TextBlock, chunk_blocks, chunk_text, split_into_blocks
from tests.unit.test_rag_integrity_final import ScriptedLlm, evidence

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text", ["áéíóú" * 300, "漢字" * 300, "x" * 1800,
                                 "https://example.invalid/" + "a?b=c&" * 300])
def test_unbroken_or_multibyte_content_is_not_one_token_and_never_emits_oversized_chunk(text):
    assert estimate_tokens(text) > 300
    with pytest.raises(ChunkBudgetExceededError) as rejected:
        chunk_text(text, chunk_size_tokens=300, overlap_tokens=50)
    assert rejected.value.code == "chunk_budget_exceeded"
    assert text not in str(rejected.value)
    assert "caracteres" in str(rejected.value) and "bytes UTF-8" in str(rejected.value)


@pytest.mark.parametrize("text", ["Autorización previa, salvo excepción escrita.", "漢字の条件。",
                                 "return valor != 0;", "https://example.invalid/a?b=1"])
def test_sentence_units_survive_spanish_cjk_code_and_urls_without_losing_tail(text):
    blocks = [TextBlock(text, "Condiciones", "pagina 1") for _ in range(35)]
    chunks = chunk_blocks(blocks, chunk_size_tokens=100, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(TextBudget.from_tokens(100).fits(chunk.text) for chunk in chunks)
    assert "\n\n".join(chunk.text for chunk in chunks) == "\n\n".join(block.text for block in blocks)


def test_token_character_and_utf8_limits_are_independent():
    assert not TextBudget(max_tokens=10000, max_chars=2, max_utf8_bytes=100).fits("abc")
    assert not TextBudget(max_tokens=10000, max_chars=10, max_utf8_bytes=5).fits("漢字")
    assert not TextBudget(max_tokens=1, max_chars=100, max_utf8_bytes=100).fits("漢")
    budget = TextBudget.from_tokens(12)
    assert budget.fits("漢字")
    assert not budget.fits_parts(["漢字"] * 3)


@pytest.mark.parametrize("kind,text", [
    ("table", "| Regla | Condicion |\n| --- | --- |\n| 12 | " + "漢" * 90 + " |"),
    ("list", "1. Solo si " + "condicion " * 90 + "sin excepcion."),
    ("code", "```python\nresultado = '" + "x" * 500 + "'\n```"),
    ("rows", "- Fila 9: Condicion: " + "á" * 200),
])
def test_indivisible_rows_conditions_list_items_and_code_lines_fail_explicitly(kind, text):
    with pytest.raises(ChunkBudgetExceededError):
        chunk_blocks([TextBlock(text, "Alcance", kind=kind)], chunk_size_tokens=100, overlap_tokens=10)


def test_long_list_preserves_complete_items_and_conditions_once():
    rows = [f"{i}. Se admite únicamente si existe aprobación escrita {i}; nunca de forma automática."
            for i in range(20)]
    chunks = chunk_blocks([TextBlock("\n".join(rows), "Permisos", kind="list")],
                          chunk_size_tokens=100, overlap_tokens=0)
    assert Counter(line for chunk in chunks for line in chunk.text.splitlines()) == Counter(rows)
    assert all(TextBudget.from_tokens(100).fits(chunk.text) for chunk in chunks)


@pytest.mark.parametrize("continuation", [
    "   Solo si existe autorización escrita.",
    "Solo si existe autorización escrita.",
    "\n   Solo si existe autorización escrita.",
    "   - Sujeto a autorización.\n     No aplica al personal temporal.",
])
def test_list_continuation_and_nested_condition_remain_with_their_item(continuation):
    first = "1. Se concede el permiso.\n" + continuation
    text = first + "\n2. Registrar la solicitud.\n\nOtro apartado independiente."
    blocks = split_into_blocks(text)
    assert blocks[0].kind == "list" and first in blocks[0].text
    assert blocks[-1].kind == "paragraph"
    # Presupuesto justo para la primera unidad; el segundo elemento no la fragmenta.
    limit = estimate_tokens(first)
    chunks = chunk_blocks(blocks[:1], chunk_size_tokens=limit, overlap_tokens=0)
    assert chunks[0].text == first
    assert chunks[1].text == "2. Registrar la solicitud."
    with pytest.raises(ChunkBudgetExceededError):
        chunk_blocks(blocks[:1], chunk_size_tokens=limit - 1, overlap_tokens=0)


def test_indented_list_roots_do_not_become_nested_items_after_parsing():
    text = "  1. Primer permiso.\n     Solo si existe aprobación escrita.\n  2. Registrar solicitud."
    blocks = split_into_blocks(text)
    assert blocks[0].text.startswith("  1.")
    first = "  1. Primer permiso.\n     Solo si existe aprobación escrita."
    chunks = chunk_blocks(blocks, chunk_size_tokens=estimate_tokens(first), overlap_tokens=0)
    assert len(chunks) == 2 and "Solo si" in chunks[0].text
    assert chunks[1].text == "2. Registrar solicitud."


def test_cjk_context_is_rejected_before_runtime_even_under_old_character_ceiling():
    llm = ScriptedLlm([])
    agent = KnowledgeAgent(llm=llm, policy=ModelPolicy())
    model = agent._policy.fast_model
    text = "漢" * 2500
    assert len(text) < agent._input_budget_chars(model, Intent.GENERAL)
    with pytest.raises(InferenceFailureError) as error:
        agent._chat_with_fallback(model_name=model, fallback_model_name=model,
                                  messages=[{"role": "user", "content": text}], intent=Intent.GENERAL)
    assert error.value.failure_kind is InferenceFailureKind.CONTEXT_LIMIT
    assert not llm.calls


def test_complete_spanish_evidence_displaces_large_cjk_memory(monkeypatch):
    monkeypatch.setattr(get_settings(), "answer_evidence_mode", "extractive")
    item = evidence("La autorización requiere aprobación escrita y no aplica al personal temporal.")
    llm = ScriptedLlm([f"{item.text} [[{item.source_id}]]"])
    agent = KnowledgeAgent(llm=llm, policy=ModelPolicy())
    result = agent.synthesize(question="¿Qué requiere la autorización?", evidences=(item,),
                              memory=ConversationContext("fixture", summary="漢" * 5000),
                              model_name=agent._policy.fast_model)
    assert result.grounding.extractive_verified
    content = "\n".join(message["content"] for message in llm.calls[0]["messages"])
    assert item.text in content and "漢" not in content


def test_summary_partition_includes_full_metadata_and_cjk_question():
    agent = KnowledgeAgent(llm=ScriptedLlm([]), policy=ModelPolicy())
    item = replace(evidence("Condición completa."), section="漢" * 3000)
    with pytest.raises(InferenceFailureError):
        agent._partition_summary_evidence((item,), model_name=agent._policy.fast_model)
    with pytest.raises(InferenceFailureError):
        agent._partition_summary_evidence((evidence("Condición completa."),),
                                           model_name=agent._policy.fast_model, question="漢" * 3000)


def test_planner_rejects_cjk_catalog_without_sending_partial_schema():
    llm = ScriptedLlm([])
    plan = build_query_plan(question="Consulta autorizada", catalog=[{
        "source": "fixture", "description": "漢" * 3000, "entities": [],
    }], llm=llm, model_name=ModelPolicy().fast_model)
    assert plan is None and not llm.calls


def test_budget_revision_invalidates_fingerprint_without_changing_models(monkeypatch):
    current = index_manifest.indexing_fingerprint()
    settings = get_settings()
    baseline = (settings.ollama_embedding_model, settings.rag_embedding_dimension, settings.ollama_fast_model)
    monkeypatch.setattr(index_manifest, "TEXT_BUDGET_VERSION", "legacy-word-count")
    assert index_manifest.indexing_fingerprint() != current
    assert (settings.ollama_embedding_model, settings.rag_embedding_dimension, settings.ollama_fast_model) == baseline
