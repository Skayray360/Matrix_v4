# Creado por Aldo Garcia.
"""M6: datos empresariales sin fuentes y diagnostico del evaluador, sin LLM real."""

from unittest.mock import MagicMock

import pytest

from app.common.answers import GENERAL_HEADING, safe_nonfactual_text
from app.config import get_settings
from app.llm.model_policy import ModelPolicy
from app.rag.retriever import RetrievalResult
from scripts import rag_eval
from tests.conftest import make_context
from tests.unit.test_implementation_v2 import sql
from tests.unit.test_rag_eval_annotations import _mock_runtime, _outcome
from tests.unit.test_rh_routing import ControlledLlm, services

__all__ = ["sql"]
pytestmark = pytest.mark.unit
CASES = {case["id"]: case for case in rag_eval.load_cases()}


@pytest.fixture(autouse=True)
def general_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "answer_allow_general_knowledge", True)
    monkeypatch.setattr(get_settings(), "answer_structured_output", False)


@pytest.mark.parametrize("question", [
    *(CASES[case_id]["question"] for case_id in ("unsup-01", "route-01", "route-05", "route-07")),
    "¿Cuál fue el presupuesto de capacitación del trimestre pasado?",
    "Explica el presupuesto de capacitación para noviembre de 2026.",
    "Dime el presupuesto autorizado de marketing.",
])
def test_specific_business_request_never_publishes_model_facts_without_sources(sql, question):
    db, _, _ = sql
    ctx = make_context(categories=frozenset({"prestaciones"}), wildcard=False)
    retriever = MagicMock()
    retriever.retrieve.return_value = RetrievalResult()
    llm = ControlledLlm("La empresa entrega 900 pesos y concede 99 días.")
    orchestrator, conversation, _, _ = services(
        db, ctx, categories=ctx.allowed_categories, retriever=retriever, llm=llm,
    )
    result = orchestrator.handle_chat(db, ctx=ctx, conversation=conversation, message=question)
    assert result.answer_basis == "insufficient"
    assert safe_nonfactual_text(result.answer)
    assert result.answer != llm.answer and result.public_sources() == []
    assert len(llm.calls) == 1
    assert "Redacta solamente una pregunta" in llm.calls[0][0]["content"]
    assert retriever.retrieve.call_args.kwargs["authorized_categories"] == ctx.allowed_categories


@pytest.mark.parametrize("question", [
    "¿Qué es un presupuesto trimestral?",
    "Cómo elaborar un presupuesto para el próximo trimestre?",
    "Explica un ejemplo de presupuesto aprobado para una empresa ficticia.",
    "Cómo organizar un descanso laboral?",
    "¿Qué es la fotosíntesis?",
])
def test_open_guidance_still_retrieves_first_and_labels_general_answer(sql, question):
    db, _, _ = sql
    ctx = make_context(categories=frozenset({"prestaciones"}), wildcard=False)
    retriever = MagicMock()
    retriever.retrieve.return_value = RetrievalResult()
    llm = ControlledLlm("Un presupuesto organiza ingresos y gastos estimados.")
    orchestrator, conversation, _, _ = services(
        db, ctx, categories=ctx.allowed_categories, retriever=retriever, llm=llm,
    )
    assert not ModelPolicy.requires_internal_evidence(question)
    result = orchestrator.handle_chat(db, ctx=ctx, conversation=conversation, message=question)
    assert result.answer_basis == "general" and not result.grounded
    assert result.answer.startswith(GENERAL_HEADING)
    assert result.public_sources() == []
    assert retriever.retrieve.call_count == 1 and len(llm.calls) == 1


@pytest.mark.parametrize("answer,sources,expected_reasons", [
    ("No dispongo de evidencia suficiente.", [], ()),
    ("El presupuesto es 900 pesos.", [], ("no declaro insuficiencia",)),
    ("No dispongo de evidencia suficiente.", ["prestaciones/irrelevante.md#0"],
     ("recupero fuentes",)),
    ("No dispongo de evidencia suficiente.", ["nomina/restringido.md#0"],
     ("recuperacion fuera del alcance", "recupero fuentes")),
    ("El presupuesto es 900 pesos.", ["nomina/restringido.md#0"],
     ("no declaro insuficiencia", "recuperacion fuera del alcance", "recupero fuentes")),
])
def test_unsupported_diagnostics_distinguish_abstention_retrieval_and_acl(
    monkeypatch, answer, sources, expected_reasons,
):
    _mock_runtime(monkeypatch, _outcome(answer, sources, grounded=False))
    report = rag_eval.evaluate_full([CASES["unsup-01"]])
    result = report.results[0]
    assert result.passed is (not expected_reasons)
    assert result.answer_pass is (not expected_reasons)
    assert report.metrics()["unsupported_claim_rate_pct"] == (100 if expected_reasons else 0)
    assert all(reason in result.detail for reason in expected_reasons)
    assert ("no declaro insuficiencia" in result.detail) is ("no declaro insuficiencia" in expected_reasons)
    assert ("recuperacion fuera del alcance" in result.detail) is (
        "recuperacion fuera del alcance" in expected_reasons
    )
