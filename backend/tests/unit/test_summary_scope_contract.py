# Creado por Aldo Garcia.
"""Regresion del resumen de portabilidad: alcance y formato son contratos distintos.

Un PDF ficticio se genera en tmp_path y se extrae localmente; las inferencias son dobles finitos y
Qdrant/SQL usan memoria temporal. No se conecta a los servicios del operador.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.agents.knowledge_agent import KnowledgeAgent
from app.common.errors import AnswerValidationError
from app.config import get_settings
from app.ingestion.loaders import extract_pdf
from app.llm.model_policy import Intent, ModelPolicy
from app.llm.ollama_client import ChatResult
from app.rag.chunking import chunk_blocks
from app.rag.schemas import Evidence
from app.rag.summary_scope import (
    requested_summary_points,
    requested_summary_section,
    select_summary_scope,
    summary_map_question,
)
from tests.unit import test_document_overview_retrieval_20261008 as overview

pytestmark = pytest.mark.unit
QUESTION = (
    'Según “Guía del Programa Horizonte por Jubilación de diciembre de 2041”, '
    'resume la sección de portabilidad en cinco puntos. Indica las páginas utilizadas.'
)


@pytest.fixture
def corpus(monkeypatch):
    yield from overview.corpus.__wrapped__(monkeypatch)


@pytest.fixture
def synthetic_program_pdf(tmp_path):
    """Corpus inventado con capítulos, continuación y localizadores reales de PDF."""
    pages = [
        "Antecedentes\nEste programa ficticio sirve para probar el alcance de resúmenes.",
        "Fondo Prueba Verde\nEste capítulo ajeno no pertenece a portabilidad.",
        *[f"Capítulo ficticio {number}\nMaterial de ejemplo del apartado {number}." for number in range(3, 9)],
        "Portabilidad del Programa Horizonte\nLa cuenta de ejemplo conserva las unidades consolidadas.\n"
        "Las unidades pendientes siguen sujetas al calendario ficticio.",
        "Portabilidad del Programa Horizonte\nEl saldo ilustrativo puede trasladarse al Fondo Prueba Azul.\n"
        "La solicitud de ejemplo requiere un formato firmado por el participante.",
    ]
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        content = bytearray(b"BT /F1 11 Tf 40 750 Td 16 TL\n")
        for line in text.splitlines():
            # Octales para los bytes no ASCII y metacaracteres del literal PDF.
            escaped = "".join(
                chr(value) if 32 <= value <= 126 and chr(value) not in "()\\" else f"\\{value:03o}"
                for value in line.encode("cp1252")
            )
            content.extend(f"({escaped}) Tj T*\n".encode("ascii"))
        content.extend(b"ET")
        stream = DecodedStreamObject()
        stream.set_data(bytes(content))
        page[NameObject("/Contents")] = stream
    filename = "Guía del Programa Horizonte por Jubilación de diciembre de 2041.pdf"
    path = tmp_path / filename
    writer.write(path)
    return filename, chunk_blocks(extract_pdf(path.read_bytes()).blocks, chunk_size_tokens=900, overlap_tokens=120)


def evidence(text, index=0, *, section="Portabilidad", page=None):
    return Evidence(
        source_id=f"general/manual.pdf#{index}", text=text, score=1.0, category="general",
        filename="manual.pdf", section=section, page_or_sheet=page or f"pagina {index + 1}",
        document_id="allowed-document", chunk_id=f"chunk-{index}",
    )


def json_answer(items):
    return json.dumps({"status": "answered", "claims": [
        {"text": item.text, "citations": [f"E{index + 1}"]} for index, item in enumerate(items)
    ], "clarification": ""})


class Client:
    def __init__(self, *answers):
        self.answers, self.calls = iter(answers), []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return ChatResult(content=next(self.answers), model=kwargs["model"], latency_ms=1)


@pytest.fixture(autouse=True)
def cited(monkeypatch):
    monkeypatch.setattr(get_settings(), "answer_evidence_mode", "cited")
    monkeypatch.setattr(get_settings(), "answer_structured_output", True)


def synthesize(client, sources, question=QUESTION, monkeypatch=None):
    policy = ModelPolicy()
    agent = KnowledgeAgent(llm=client, policy=policy)
    if monkeypatch:
        monkeypatch.setattr(agent, "_summary_exceeds_context", lambda *_a, **_k: False)
    return agent.synthesize(question=question, evidences=sources, model_name=policy.fast_model,
                            intent=Intent.DOCUMENT_SUMMARY)


def test_exact_request_has_explicit_section_and_five_points():
    assert requested_summary_section(QUESTION) == "portabilidad"
    assert requested_summary_points(QUESTION) == 5


@pytest.mark.parametrize("scope", ["corporate", "conversation"])
def test_synthetic_pdf_section_retrieval_excludes_unrequested_pages(corpus, synthetic_program_pdf, scope):
    filename, blocks = synthetic_program_pdf
    chunks = corpus.publish(filename, scope_name=scope, blocks=blocks)
    # Mismo titulo fuera del alcance: ni siquiera entra a las identidades.
    corpus.publish(filename, scope_name=scope, user="other-owner", category="restricted", blocks=blocks)
    result = corpus.retriever.retrieve(
        ctx=corpus.ctx, question=QUESTION, authorized_categories=frozenset({"tecnologia"}),
        summary=True, include_private=scope == "conversation", conversation_id="thread",
    )
    assert {item.page_or_sheet for item in result.evidences} == {"pagina 9", "pagina 10"}
    assert {item.document_id for item in result.evidences} == {chunks[0].metadata.document_id}
    assert all(item.text.startswith("Portabilidad") for item in result.evidences)
    assert not result.clarification and not result.truncated
    assert result.fetched > len(result.evidences)


def test_whole_pdf_summary_still_contains_all_pages(corpus, synthetic_program_pdf):
    filename, blocks = synthetic_program_pdf
    chunks = corpus.publish(filename, scope_name="corporate", blocks=blocks)
    result = corpus.retriever.retrieve(
        ctx=corpus.ctx, question=f"Resume {filename}", authorized_categories=frozenset({"tecnologia"}),
        summary=True, include_private=False,
    )
    assert {item.source_id for item in result.evidences} == {item.metadata.source_id for item in chunks}


def test_no_named_section_does_not_fall_back_to_entire_document(corpus):
    corpus.publish("Manual de equipos.docx", scope_name="corporate")
    result = corpus.retriever.retrieve(
        ctx=corpus.ctx, question="Resume la sección de garantías del documento Manual de equipos.docx",
        authorized_categories=frozenset({"tecnologia"}), summary=True, include_private=False,
    )
    assert not result.evidences and "encabezado exacto" in result.clarification


def test_body_mentions_do_not_impersonate_a_section_and_complete_page_is_preserved():
    unrelated = evidence("Antecedentes\nLa portabilidad se explica posteriormente.", section="pagina 1")
    heading = evidence("Portabilidad del contrato\nSe conserva el saldo.", 1, section="pagina 2")
    continuation = evidence("Excepto las cuotas no consolidadas.", 2, section="pagina 2", page="pagina 2")
    selection = select_summary_scope("Resume la sección de portabilidad en 5 puntos", (unrelated, heading, continuation))
    assert selection.evidences == (heading, continuation)


@pytest.mark.parametrize("question,title", [
    ('Resume el apartado “Control de acceso” en tres puntos.', "control de acceso"),
    ("Resume la sección de seguridad con citas.", "seguridad"),
    ("Resume el capítulo Recuperación del documento Manual de soporte.pdf", "recuperacion"),
])
def test_section_names_are_generic_and_keep_internal_prepositions(question, title):
    assert requested_summary_section(question) == title


def test_maps_keep_scope_and_remove_only_final_point_count():
    question = summary_map_question(QUESTION, part=1, total=2)
    assert "portabilidad" in question
    assert "Horizonte" in question
    assert requested_summary_section(question) == "portabilidad"
    assert requested_summary_points(question) is None


def test_valid_five_claim_summary_has_exact_five_visible_points():
    sources = tuple(evidence(text, index) for index, text in enumerate([
        "La cuenta conserva las aportaciones.", "La regla distingue cohortes de ingreso.",
        "La tabla depende de la antigüedad.", "Las transferencias tienen condiciones.",
        "Los fondos se destinan al retiro.",
    ]))
    client = Client(json_answer(sources))
    result = synthesize(client, sources)
    assert len(re.findall(r"(?m)^\d+\. ", result.answer)) == 5
    assert result.grounding.grounded and len(client.calls) == 1
    assert set(result.cited_source_ids) == {source.source_id for source in sources}


def test_incorrect_point_count_is_retried_before_it_is_published():
    sources = tuple(evidence(f"Condición del componente {word}.", index)
                    for index, word in enumerate(["alfa", "beta", "gamma", "delta", "epsilon"]))
    client = Client(json_answer(sources[:1]), json_answer(sources))
    result = synthesize(client, sources)
    assert result.regenerated and len(client.calls) == 2
    assert len(re.findall(r"(?m)^\d+\. ", result.answer)) == 5


def test_failed_model_discloses_fewer_extracts_and_never_dumps_unrequested_pages(synthetic_program_pdf, monkeypatch):
    filename, blocks = synthetic_program_pdf
    sources = tuple(replace(evidence(block.text, index, section=block.section, page=block.page_or_sheet),
                            filename=filename) for index, block in enumerate(blocks))
    client = Client("{}", "{}")
    result = synthesize(client, sources, monkeypatch=monkeypatch)
    assert "No pude validar una sintesis en 5 puntos. Presento 2 extractos completos" in result.answer
    assert result.grounding.extractive_verified and result.regenerated
    assert "Antecedentes" not in result.answer and "Fondo Prueba Verde" not in result.answer
    assert set(result.cited_source_ids) == {item.source_id for item in sources
                                           if item.page_or_sheet in {"pagina 9", "pagina 10"}}
    assert all("Antecedentes" not in str(call["messages"]) for call in client.calls)


def test_ambiguous_sources_are_rejected_before_section_filtering():
    original = evidence("Texto autorizado.")
    shadow = replace(original, section="Fuera del pedido", text="Texto ajeno.", document_id="other-document")
    with pytest.raises(AnswerValidationError):
        synthesize(Client(), (original, shadow))


def test_hierarchy_keeps_continuations_and_point_format_when_reduce_fails(monkeypatch):
    sources = tuple(evidence(f"Condición del componente {word}.", index,
                             section="Portabilidad" if index == 0 else "pagina 1", page="pagina 1")
                    for index, word in enumerate(["alfa", "beta", "gamma", "delta", "epsilon"]))
    client = Client(json_answer(sources[:2]), json_answer(sources[2:]), "{}")
    policy = ModelPolicy()
    agent = KnowledgeAgent(llm=client, policy=policy)
    monkeypatch.setattr(agent, "_summary_exceeds_context", lambda evidence, **_: len(evidence) == 5)
    monkeypatch.setattr(agent, "_partition_summary_evidence", lambda *_, **__: (sources[:2], sources[2:]))
    result = agent.synthesize(question=QUESTION, evidences=sources, model_name=policy.fast_model,
                              intent=Intent.DOCUMENT_SUMMARY)
    assert result.hierarchical and result.map_batches == 2
    assert result.grounding.extractive_verified
    assert set(result.cited_source_ids) == {item.source_id for item in sources}
    assert len(re.findall(r"(?m)^\d+\. ", result.answer)) == 5
    assert "Resumen consolidado por secciones" not in result.answer
    assert len(client.calls) == 3
    assert all("portabilidad" in str(call["messages"]) for call in client.calls)
