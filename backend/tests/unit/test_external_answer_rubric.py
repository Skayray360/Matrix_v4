# Creado por Aldo Garcia.
"""Evaluador externo: dobles sinteticos; no llama modelos, BD ni servicios."""

from __future__ import annotations

import re
from copy import deepcopy

import pytest

from scripts import rag_eval
from scripts.answer_rubric import assess_answer, dataset_fingerprint, partition_cases, validate_rubric
from tests.unit.test_rag_eval_annotations import _mock_runtime, _outcome

pytestmark = pytest.mark.unit


@pytest.fixture
def cases():
    return {case["id"]: case for case in rag_eval.load_cases()}


def reference_answer(case):
    rubric = case["answer_rubric"]
    if rubric["kind"] == "abstention":
        return rubric["accepted_answers"][0]
    return " ".join(
        fact["accepted_statements"][0] + " " + " ".join(f"[[{sid}]]" for sid in fact["source_ids"])
        for fact in rubric["required_facts"]
    )


def swapped_citations(text):
    first, second = re.findall(r"\[\[([^\]]+)\]\]", text)
    return text.replace(first, "TEMP_CITATION").replace(second, first).replace("TEMP_CITATION", second)


def test_every_external_annotation_accepts_its_complete_reference(cases):
    annotated = [case for case in cases.values() if "answer_rubric" in case]
    assert len(annotated) == 11
    for case in annotated:
        assessment = assess_answer(case, reference_answer(case), case.get("expected_source_ids", []))
        assert assessment.passed is True, (case["id"], assessment)


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("corresponden", "no corresponden"),
    lambda text: text.replace("20 días", "120 días"),
    lambda text: text.replace("20 días", "-20 días"),
    lambda text: text.replace("20 días", ">=20 días"),
    lambda text: text.replace("20 días", "20.5 días"),
    lambda text: text.replace("20 días", "20 meses"),
    lambda text: text.replace("5 años", "6 años"),
    lambda text: text.replace("Con 5 años de antigüedad ", ""),
    lambda text: text + " Se pagan también 900 pesos.",
    lambda text: text.replace("[[prestaciones/politica-vacaciones.md#1]]", ""),
    lambda text: text.replace("[[prestaciones/politica-vacaciones.md#1]]", "[[otra/fuente.md#0]]"),
])
def test_number_keyword_and_product_grounded_cannot_hide_wrong_answers(cases, monkeypatch, mutation):
    case = cases["prest-01"]
    answer = mutation(reference_answer(case))
    # El contrato anterior any(keyword) + grounded aceptaba estos numeros/textos.
    assert "20" in answer
    _mock_runtime(monkeypatch, _outcome(answer, case["expected_source_ids"], grounded=True))
    report = rag_eval.evaluate_full([case])
    result = report.results[0]
    assert result.grounding_pass is True
    assert result.passed is True  # Solo el contrato del producto, rotulado como tal.
    assert result.external_accuracy_pass is False
    assert report.metrics()["external_accuracy_pct"] == 0
    assert not rag_eval.quality_gate(report)["passed"]


def test_independent_correctness_does_not_depend_on_product_grounding(cases, monkeypatch):
    case = cases["prest-01"]
    _mock_runtime(monkeypatch, _outcome(reference_answer(case), case["expected_source_ids"], grounded=False))
    report = rag_eval.evaluate_full([case])
    assert report.results[0].external_accuracy_pass is True
    assert report.results[0].grounding_pass is False
    assert report.results[0].passed is False
    assert not rag_eval.quality_gate(report)["passed"]


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("5 días", "TEMP días").replace("20 días", "5 días").replace("TEMP días", "20 días"),
    lambda text: text.replace("máximo de 5", "mínimo de 5"),
    lambda text: text.replace("días hábiles", "días naturales"),
    lambda text: text.split("La investigación")[0],
    swapped_citations,
])
def test_comparison_binds_values_units_conditions_and_sources_to_each_concept(cases, mutation):
    case = cases["comp-01"]
    assert assess_answer(case, mutation(reference_answer(case)), case["expected_source_ids"]).passed is False


def test_all_required_non_numeric_facts_and_complete_date_are_required(cases):
    equipment = cases["sal-02"]
    assert not assess_answer(equipment, reference_answer(equipment).replace("lentes y ", ""),
                             equipment["expected_source_ids"]).passed
    date = cases["prest-04"]
    assert not assess_answer(date, reference_answer(date).replace("de diciembre", "de noviembre"),
                             date["expected_source_ids"]).passed


def test_formatting_and_accent_variants_are_supported_without_relaxing_claims(cases):
    case = cases["prest-01"]
    answer = reference_answer(case).replace("20 días", "**20 dias**").replace("Con", "CON")
    assert assess_answer(case, answer, case["expected_source_ids"]).passed is True


@pytest.mark.parametrize("addition", [" El presupuesto es 900 pesos.", " [[nomina/politica.md#0]]"])
def test_abstention_must_be_complete_without_added_claims_or_citations(cases, addition):
    case = cases["unsup-01"]
    assert assess_answer(case, reference_answer(case) + addition, []).passed is False


def test_irrelevant_retrieval_does_not_change_independent_abstention_correctness(cases, monkeypatch):
    case = cases["unsup-01"]
    answer = reference_answer(case)
    assert assess_answer(case, answer, ["prestaciones/unrelated.md#0"]).passed is True
    _mock_runtime(monkeypatch, _outcome(answer, ["prestaciones/unrelated.md#0"], grounded=False))
    result = rag_eval.evaluate_full([case]).results[0]
    assert result.external_accuracy_pass is True
    assert result.answer_pass is False  # Contrato del producto exige no adjuntar evidencia.


def test_correct_expected_citation_must_also_have_been_retrieved(cases):
    case = cases["prest-01"]
    answer = reference_answer(case)
    for retrieved in ([], ["prestaciones/unrelated.md#0"]):
        result = assess_answer(case, answer, retrieved)
        assert result.passed is False
        assert any("Fuentes incorrectas" in reason for reason in result.reasons)


def test_missing_annotations_are_unmeasured_and_never_approve_full_quality(cases, monkeypatch):
    case = cases["prest-03"]
    assert "answer_rubric" not in case
    _mock_runtime(monkeypatch, _outcome("Cualquier afirmacion con 25.", case["expected_source_ids"]))
    report = rag_eval.evaluate_full([case])
    assert report.results[0].external_accuracy_pass is None
    assert report.results[0].external_status == "NOT_ANNOTATED"
    assert report.metrics()["external_accuracy_pct"] is None
    assert report.metrics()["external_coverage_pct"] == 0
    assert report.as_dict()["quality_gate"]["passed"] is False


@pytest.mark.parametrize("missing", ["concept", "value", "unit", "conditions", "source_ids", "accepted_statements"])
def test_invalid_fact_annotations_do_not_approve(missing, cases):
    case = deepcopy(cases["prest-01"])
    del case["answer_rubric"]["required_facts"][0][missing]
    assessment = assess_answer(case, "20 dias.", case["expected_source_ids"])
    assert assessment.passed is None
    assert assessment.status == "INVALID_ANNOTATION"


def test_annotation_cannot_omit_one_of_the_reviewed_sources(cases):
    case = deepcopy(cases["comp-01"])
    case["answer_rubric"]["required_facts"].pop()
    with pytest.raises(ValueError, match="todas las fuentes"):
        validate_rubric(case["answer_rubric"], case["expected_source_ids"])


def test_grounded_case_cannot_be_relabelled_as_an_abstention_to_pass(cases):
    case = deepcopy(cases["prest-01"])
    case["answer_rubric"] = deepcopy(cases["unsup-01"]["answer_rubric"])
    result = assess_answer(case, "No dispongo de evidencia suficiente.", [])
    assert result.passed is None
    assert result.status == "INVALID_ANNOTATION"


def test_default_split_is_reproducible_grouped_and_keeps_partial_coverage(cases):
    original = list(cases.values())
    reordered = deepcopy(list(reversed(original)))
    partition_cases(reordered, seed="matrix-rh-golden-v1", holdout_percent=50)
    assert {c["id"]: c["evaluation_split"] for c in reordered} == {
        c["id"]: c["evaluation_split"] for c in original
    }
    assert dataset_fingerprint(original) == dataset_fingerprint(reordered)
    assert sum(c["evaluation_split"] == "holdout" for c in original) == 22
    assert sum(c["evaluation_split"] == "tune" for c in original) == 21
    tune = {c["evaluation_group"] for c in original if c["evaluation_split"] == "tune"}
    holdout = {c["evaluation_group"] for c in original if c["evaluation_split"] == "holdout"}
    assert not tune & holdout
    assert len([c for c in original if "answer_rubric" in c]) < len(original)


def test_source_revision_change_requires_annotation_review(monkeypatch):
    monkeypatch.setattr(rag_eval, "source_revision", lambda: {"pipeline_version": -1})
    with pytest.raises(ValueError, match="revise citas y rubricas"):
        rag_eval.load_cases()


@pytest.mark.parametrize("same_source", [True, False])
def test_related_documents_and_repeated_questions_cannot_cross_groups(cases, same_source):
    a = deepcopy(cases["prest-01"])
    b = deepcopy(cases["prest-02"])
    b["evaluation_group"] = "another-group"
    if same_source:
        b["question"] = "Otra pregunta del mismo documento"
    else:
        b["expected_source_ids"] = []
    with pytest.raises(ValueError, match="fuga tune/holdout"):
        partition_cases([a, b], seed="fixed", holdout_percent=50)


def test_tune_never_passes_holdout_quality_even_with_perfect_answers(cases, monkeypatch):
    case = cases["sal-02"]
    assert case["evaluation_split"] == "tune"
    _mock_runtime(monkeypatch, _outcome(reference_answer(case), case["expected_source_ids"]), ("salud_ambiental",))
    report = rag_eval.evaluate_full([case])
    assert report.metrics()["external_accuracy_pct"] == 100
    assert report.metrics()["external_coverage_pct"] == 100
    assert not rag_eval.quality_gate(report)["passed"]


def test_retrieval_only_does_not_measure_external_answers(cases, monkeypatch):
    case = cases["prest-01"]
    _mock_runtime(monkeypatch, _outcome("", case["expected_source_ids"]))
    report = rag_eval.evaluate_retrieval([case])
    assert report.metrics()["external_accuracy_pct"] is None
    assert report.metrics()["external_coverage_pct"] is None
    assert report.as_dict()["quality_gate"]["scope"] == "retrieval_and_acl_only"


def test_cli_full_uses_holdout_and_rejects_partial_external_coverage(cases, monkeypatch, capsys):
    seen = []
    def fake_evaluate(selected):
        seen.extend(selected)
        return rag_eval.EvaluationReport(mode="full", results=[rag_eval.CaseResult(
            case_id=c["id"], user=c["user"], case_type=c["type"], passed=True,
            answer_pass=True, external_accuracy_pass=True if "answer_rubric" in c else None,
            external_status="PASS" if "answer_rubric" in c else "NOT_ANNOTATED",
            evaluation_group=c["evaluation_group"], evaluation_split=c["evaluation_split"],
        ) for c in selected])
    monkeypatch.setattr(rag_eval, "evaluate_full", fake_evaluate)
    assert rag_eval.main(["--full"]) == 1
    assert len(seen) == 22
    assert {c["evaluation_split"] for c in seen} == {"holdout"}
    assert "Cobertura externa incompleta" in capsys.readouterr().out


def test_cli_filtered_success_is_not_a_full_holdout_approval(cases, monkeypatch, capsys):
    def fake_evaluate(selected):
        return rag_eval.EvaluationReport(mode="full", results=[rag_eval.CaseResult(
            case_id=c["id"], user=c["user"], case_type=c["type"], passed=True,
            answer_pass=True, external_accuracy_pass=True, external_status="PASS",
            evaluation_group=c["evaluation_group"], evaluation_split=c["evaluation_split"],
        ) for c in selected])
    monkeypatch.setattr(rag_eval, "evaluate_full", fake_evaluate)
    assert rag_eval.main(["--full", "--limit", "1"]) == 1
    assert "sin filtros" in capsys.readouterr().out


def test_cli_can_pass_a_complete_annotated_holdout_without_trusting_grounding(cases, monkeypatch):
    selected = [cases["prest-01"], cases["sal-02"]]
    monkeypatch.setattr(rag_eval, "load_cases", lambda _path: selected)
    case = cases["prest-01"]
    _mock_runtime(monkeypatch, _outcome(reference_answer(case), case["expected_source_ids"]))
    assert rag_eval.main(["--full"]) == 0
