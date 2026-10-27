# Creado por Aldo Garcia.
"""Regresión de cinco turnos con reglas nuevas de un simulador de personajes.

Contrato de conversación, no evaluación de Gemma: SQLite y dobles finitos de
recuperación/inferencia. Los datos se crean aquí para practicar cambios de
contexto y validación numérica; no describen prestaciones ni cuentas reales.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from fractions import Fraction

import pytest
from sqlalchemy import select

from app.common.answers import UNVERIFIED_ANSWER_NOTICE, safe_nonfactual_text
from app.database.models import ConversationMessage
from app.memory.service import authorization_fingerprint
from app.rag.claim_context import declared_case
from app.rag.schemas import Evidence
from tests.unit import test_document_workflow_acceptance as acceptance

pytestmark = pytest.mark.unit
workflow = acceptance.workflow

TITLE = "Manual del simulador de personajes de marzo de 2099"
QUESTIONS = (
    f"Según “{TITLE}”, resume la sección Reglas del simulador en cinco puntos. Indica las páginas utilizadas.",
    "Según ese documento, ¿qué porcentaje corresponde a los personajes que ingresaron a partir del 3 de marzo "
    "de 2093 y tienen entre 2 y 2.99 años de antigüedad? ¿A qué puntos aplica? Cita la página.",
    "En el simulador ingresé en mayo de 2093, con 2 años y 6 meses de antigüedad. "
    "Según ese documento, ¿qué porcentaje me corresponde? Explica cómo ubicas mi antigüedad en la tabla y cita la página.",
    "Mantén mi fecha de ingreso y las demás condiciones del caso anterior, pero cambia la antigüedad a "
    "3 años y 6 meses. ¿Qué porcentaje corresponde ahora y por qué?",
    "Según ese documento, ¿a dónde se transfieren los personajes al cerrar una partida? "
    "Indica las restricciones expresas y la página.",
)

def evidence(text: str, page: int) -> Evidence:
    return Evidence(
        source_id=f"tecnologia/simulador_prueba.pdf#{page}", filename=TITLE + ".pdf", text=text,
        score=0.9, category="tecnologia", page_or_sheet=f"pagina {page}",
        document_id="synthetic-simulator", chunk_id=f"synthetic-simulator-{page}",
        section="Reglas del simulador", scope="corporate",
    )


# Datos nuevos de un simulador imaginario. La progresión 11 + 7*n no
# representa prestaciones, aportaciones ni derechos de una organización.
SYNTHETIC_ROWS = "\n".join(f"{year} – {year}.99 {11 + 7 * year}" for year in range(5))
TABLE = evidence(
    "Reglas del simulador\n"
    "Los personajes que ingresen a partir del 3 de marzo de 2093 tendrán derecho a los Puntos Luma "
    "de acuerdo a la siguiente tabla de antigüedad:\n"
    "Antigüedad %\n" + SYNTHETIC_ROWS,
    9,
)
TRANSFER = evidence(
    "Reglas del simulador\n"
    "Al cerrar una partida, los personajes se transfieren a la Sala Verde (Sólo modo ensayo) "
    "o a la Sala Violeta del simulador.", 10,
)
ANTECEDENTS = replace(
    evidence("Antecedentes\nEl simulador fue creado para practicar recorridos de personajes.", 1),
    section="Antecedentes",
)


def response(*claims: tuple[str, str]) -> str:
    return json.dumps({
        "status": "answered", "claims": [{"text": text, "citations": [alias]} for text, alias in claims],
        "clarification": "",
    }, ensure_ascii=False)


def case_response(years: int, percentage: int) -> str:
    return response((
        f"Si los datos declarados son correctos, para {years} años y 6 meses, equivalentes a {years}.5 años, "
        f"corresponde el {percentage}% a los Puntos Luma; "
        f"esta antigüedad se ubica en el intervalo de {years} a {years}.99 años.", "E1",
    ))


SUMMARY_RESPONSE = response(
    ("La sección describe la tabla del simulador.", "E1"),
    ("La condición depende de la fecha de ingreso del personaje.", "E1"),
    ("La tabla de antigüedad determina el porcentaje de los Puntos Luma.", "E1"),
    ("Al cerrar una partida, los personajes se transfieren a la Sala Verde, sólo modo ensayo.", "E2"),
    ("También pueden transferirse a la Sala Violeta del simulador.", "E2"),
)
ROW_RESPONSE = response((
    "Para los personajes que ingresen a partir del 3 de marzo de 2093, el intervalo de 2 a 2.99 años "
    "corresponde al 25% de los Puntos Luma.", "E1",
))
TRANSFER_RESPONSE = response((TRANSFER.text.split("\n", 1)[1], "E2"))


def configure(workflow):
    workflow.retriever.retrieve.return_value = acceptance.recovered((TABLE, TRANSFER))
    # La recuperación amplia puede entregar páginas ajenas a la sección
    # solicitada: la síntesis debe acotar las fuentes, no resumir todo el PDF.
    workflow.retriever.retrieve_attachment_summary.return_value = acceptance.recovered((ANTECEDENTS, TABLE, TRANSFER))
    original_chat = workflow.llm.chat

    def answer_using_the_aliases_actually_received(**kwargs):
        result = original_chat(**kwargs)
        # E1/E2 del fixture significan tabla/transferencia, no posiciones
        # impuestas al recuperador. Reetiquetar como haría un generador evita
        # confundir un fallo de selección de sección con una cita desplazada.
        labels = {source_id: alias for alias, source_id in re.findall(
            r"\[cita: \[\[(E\d+)\]\]\] \[source_id: ([^\]]+)\]",
            kwargs["messages"][-1]["content"],
        )}
        payload = json.loads(result.content)
        sources = {"E1": TABLE.source_id, "E2": TRANSFER.source_id}
        for claim in payload["claims"]:
            claim["citations"] = [labels[sources[alias]] for alias in claim["citations"]]
        return replace(result, content=json.dumps(payload, ensure_ascii=False))

    workflow.llm.chat = answer_using_the_aliases_actually_received


def seed_history(workflow, questions):
    """Persiste turnos previos autorizados sin ejecutar los otros comportamientos."""
    scope = authorization_fingerprint(workflow.db, workflow.ctx, workflow.ctx.allowed_categories)
    workflow.db.info["authorization_scope"] = scope
    # Contextos ya normalizados, como los persiste el orquestador después de
    # cada respuesta. Así el recorte legítimo de la memoria no borra el título
    # porque desapareció el primer turno; no invocamos el código bajo prueba.
    contexts = [question.replace("ese documento", f"“{TITLE}”") for question in QUESTIONS]
    contexts[3] = contexts[2].replace("2 años y 6 meses", "3 años y 6 meses")
    for index, question in enumerate(questions):
        workflow.memory.append_message(
            workflow.db, workflow.conversation, role="user", content=question,
            authorized_categories=("tecnologia",), context_query=contexts[index],
        )
        workflow.memory.append_message(
            workflow.db, workflow.conversation, role="assistant", content="Respuesta documental previa.",
            answer_basis="documented", source_ids=(TABLE.source_id,), authorized_categories=("tecnologia",),
        )
    workflow.db.commit()


def current_query(workflow):
    return workflow.db.scalars(select(ConversationMessage.context_query).where(
        ConversationMessage.role == "user",
    ).order_by(ConversationMessage.seq.desc())).first()


def test_five_questions_publish_focused_sources_and_update_only_the_current_case(workflow):
    configure(workflow)
    workflow.llm.responses = [SUMMARY_RESPONSE, ROW_RESPONSE, case_response(2, 25), case_response(3, 32), TRANSFER_RESPONSE]
    results, queries = [], []
    for question in QUESTIONS:
        results.append(workflow.chat(question))
        queries.append(current_query(workflow))
    assert all(item.grounded and not item.regenerated for item in results)
    assert set(results[0].cited_source_ids) == {TABLE.source_id, TRANSFER.source_id}
    assert all(item["page_or_sheet"] in {"pagina 9", "pagina 10"} for item in results[0].public_sources())
    assert "Antecedentes" not in results[0].answer
    assert "25%" in results[1].answer and "25%" in results[2].answer
    assert "32%" in results[3].answer and "25%" not in results[3].answer
    assert "Sala Verde" in results[4].answer and "Sala Violeta" in results[4].answer
    assert results[4].cited_source_ids == (TRANSFER.source_id,)
    assert declared_case(queries[2]).tenure == Fraction(5, 2)
    assert declared_case(queries[3]).tenure == Fraction(7, 2)
    assert "2 y 2.99" not in queries[2] and "2 años y 6 meses" not in queries[3]
    assert declared_case(queries[4]).tenure is None and declared_case(queries[4]).entry is None
    assert TITLE in queries[4] and "Mantén" not in queries[4]
    assert len(workflow.llm.calls) == 5
    assert len(workflow.stored_assistant_answers()) == 5


def test_quoted_title_section_summary_uses_only_requested_section_and_five_points(workflow):
    configure(workflow)
    workflow.llm.responses = [SUMMARY_RESPONSE]
    result = workflow.chat(QUESTIONS[0])
    assert result.grounded and not result.regenerated
    assert len(result.cited_source_ids) == 2
    assert set(result.cited_source_ids) == {TABLE.source_id, TRANSFER.source_id}
    assert "Antecedentes" not in result.answer
    assert "El simulador fue creado" not in workflow.llm.calls[0]["messages"][-1]["content"]
    assert re.findall(r"(?m)^([1-5])\. ", result.answer) == ["1", "2", "3", "4", "5"]
    assert len(workflow.llm.calls) == 1


def test_complete_personal_case_drops_the_previous_table_range_from_authorized_memory(workflow):
    configure(workflow)
    seed_history(workflow, QUESTIONS[:2])
    workflow.llm.responses = [case_response(2, 25)] * 2
    result = workflow.chat(QUESTIONS[2])
    query = current_query(workflow)
    assert result.grounded and not result.regenerated
    assert declared_case(query).tenure == Fraction(5, 2)
    assert TITLE in query and "2 y 2.99" not in query and "resume la sección" not in query
    assert len(workflow.llm.calls) == 1


def test_explicit_change_preserves_entry_replaces_tenure_and_applies_new_row(workflow):
    configure(workflow)
    seed_history(workflow, QUESTIONS[:3])
    workflow.llm.responses = [case_response(3, 32)] * 2
    result = workflow.chat(QUESTIONS[3])
    query = current_query(workflow)
    case = declared_case(query)
    assert result.grounded and not result.regenerated
    assert case.tenure == Fraction(7, 2) and not case.before_retirement
    assert case.entry == declared_case(QUESTIONS[2]).entry
    assert "2 años y 6 meses" not in query and TITLE in query
    assert "32%" in result.answer and len(workflow.llm.calls) == 1


def test_transfer_followup_carries_the_document_but_no_prior_personal_case(workflow):
    configure(workflow)
    seed_history(workflow, QUESTIONS[:4])
    workflow.llm.responses = [TRANSFER_RESPONSE] * 2
    result = workflow.chat(QUESTIONS[4])
    query = current_query(workflow)
    assert result.grounded and not result.regenerated
    assert TITLE in query
    assert declared_case(query).tenure is None and declared_case(query).entry is None
    assert "2 y 2.99" not in query and "3 años y 6 meses" not in query and "resume la sección" not in query
    assert result.cited_source_ids == (TRANSFER.source_id,)
    assert len(workflow.llm.calls) == 1


@pytest.mark.parametrize("index,years,wrong_percentage", [(2, 2, 26), (3, 3, 25)])
def test_a_valid_citation_does_not_authorize_wrong_or_stale_case_percentage(workflow, index, years, wrong_percentage):
    configure(workflow)
    seed_history(workflow, QUESTIONS[:index])
    workflow.llm.responses = [case_response(years, wrong_percentage)] * 2
    previous_answers = workflow.stored_assistant_answers()
    result = workflow.chat(QUESTIONS[index])
    # Ninguno de los dos borradores contiene una afirmacion del caso validada.
    # La limitacion se publica como tal, sin cifras reparadas ni citas decorativas.
    assert result.regenerated and result.answer_basis == "insufficient"
    assert not result.grounded and safe_nonfactual_text(result.answer)
    assert result.answer.startswith(UNVERIFIED_ANSWER_NOTICE)
    assert "%" not in result.answer and f"{wrong_percentage}%" not in result.answer
    assert result.cited_source_ids == () and result.public_sources() == []
    assert workflow.stored_assistant_answers() == previous_answers + [result.answer]
    assert len(workflow.llm.calls) == 2
    assert all("response_schema" in call for call in workflow.llm.calls)
