# Creado por Aldo Garcia.
"""Oraculo determinista independiente para respuestas sinteticas anotadas.

No importa validadores ni decisiones del producto. Compara enunciados completos
con variantes escritas en la referencia, exige todos los hechos y vincula las
citas a cada enunciado. Una parafrasis no anotada se rechaza conservadoramente:
esto NO verifica semantica universal ni sustituye revision humana del dominio.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

LIMITATIONS = (
    "Referencia sintetica; no validada por un experto del dominio.",
    "Solo reconoce variantes completas anotadas; puede rechazar parafrasis correctas.",
    "Comprueba hechos y citas anotados, no veracidad semantica universal.",
)
_CITATION = re.compile(r"\[\[([^\[\]\r\n]{1,300})\]\]")
_PARTS = re.compile(r"\[\[([^\[\]\r\n]{1,300})\]\]|([.!?;](?!\d)|\n)")


@dataclass(frozen=True)
class Assessment:
    passed: bool | None
    status: str
    reasons: tuple[str, ...] = ()
    matched_facts: tuple[str, ...] = ()


def normalize(text: str) -> str:
    """Ignora acentos/caja/Markdown simple, conserva signos y numeros completos."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))
    text = text.replace("**", "").replace("`", "")
    # No convertir -20, 20%, 20.5, >=20 o 120 en el valor 20.
    return " ".join(re.findall(r"[+-]?\d+(?:[.,]\d+)*|[^\W\d_]+|[^\w\s]", text)).strip()


def _statements(answer: str) -> list[tuple[str, set[str]]]:
    result: list[tuple[str, set[str]]] = []
    current = ""
    citations: set[str] = set()
    position = 0
    for match in _PARTS.finditer(answer):
        current += answer[position:match.start()]
        if match.group(1) is not None:
            if not current.strip() and result:
                result[-1][1].add(match.group(1))
            else:
                citations.add(match.group(1))
        elif current.strip():
            result.append((normalize(current.strip()), citations))
            current, citations = "", set()
        position = match.end()
    current += answer[position:]
    if current.strip() or citations:
        result.append((normalize(current.strip()), citations))
    return result


def _strings(value: Any, *, nonempty: bool = False) -> bool:
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(item, str) and item.strip() for item in value))


def validate_rubric(rubric: Any, expected_sources: list[str], *, case_type: str | None = None) -> None:
    """Falla con anotaciones incompletas; jamas las convierte en aprobacion."""
    if not isinstance(rubric, dict) or rubric.get("version") != 1:
        raise ValueError("rubrica: version requerida 1")
    if not _strings(rubric.get("forbidden_terms")):
        raise ValueError("rubrica: forbidden_terms debe ser una lista explicita")
    kind = rubric.get("kind")
    if case_type is not None and kind != ("facts" if case_type == "grounded" else "abstention"):
        raise ValueError("rubrica: tipo incompatible con el caso")
    if kind == "abstention":
        if not _strings(rubric.get("accepted_answers"), nonempty=True):
            raise ValueError("rubrica: abstencion sin respuestas completas anotadas")
        if expected_sources or any(_CITATION.search(text) for text in rubric["accepted_answers"]):
            raise ValueError("rubrica: una abstencion no puede requerir fuentes ni incluir citas")
        return
    facts = rubric.get("required_facts")
    if kind != "facts" or not isinstance(facts, list) or not facts:
        raise ValueError("rubrica: se requieren hechos obligatorios")
    ids: set[str] = set()
    statements: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("rubrica: hecho invalido")
        for key in ("id", "concept", "value"):
            if not isinstance(fact.get(key), str) or not fact[key].strip():
                raise ValueError(f"rubrica: hecho sin {key}")
        if fact["id"] in ids:
            raise ValueError("rubrica: id de hecho duplicado")
        ids.add(fact["id"])
        if "unit" not in fact or not (fact["unit"] is None or
                                       isinstance(fact["unit"], str) and fact["unit"].strip()):
            raise ValueError("rubrica: unidad explicita requerida (null para hechos cualitativos)")
        if not _strings(fact.get("conditions")):
            raise ValueError("rubrica: condiciones explicitas requeridas")
        if not _strings(fact.get("source_ids"), nonempty=True) or not set(fact["source_ids"]) <= set(expected_sources):
            raise ValueError("rubrica: fuentes del hecho fuera de la referencia")
        if not _strings(fact.get("accepted_statements"), nonempty=True):
            raise ValueError("rubrica: faltan enunciados completos")
        for statement in fact["accepted_statements"]:
            parts = _statements(statement)
            if len(parts) != 1 or parts[0][1] or not parts[0][0] or parts[0][0] in statements:
                raise ValueError("rubrica: enunciado ambiguo, repetido o con citas embebidas")
            statements.add(parts[0][0])
            for component in (fact["concept"], fact["value"], fact["unit"], *fact["conditions"]):
                if component and f" {normalize(component)} " not in f" {parts[0][0]} ":
                    raise ValueError("rubrica: enunciado no contiene concepto/valor/unidad/condiciones")
    if {sid for fact in facts for sid in fact["source_ids"]} != set(expected_sources):
        raise ValueError("rubrica: no cubre todas las fuentes requeridas")


def assess_answer(case: dict[str, Any], answer: str, retrieved_sources: list[str]) -> Assessment:
    rubric = case.get("answer_rubric")
    if rubric is None:
        return Assessment(None, "NOT_ANNOTATED", ("Falta rubrica externa; keywords no son exactitud.",))
    try:
        validate_rubric(rubric, case.get("expected_source_ids", []), case_type=case.get("type"))
    except ValueError as error:
        return Assessment(None, "INVALID_ANNOTATION", (str(error),))
    if not answer.strip() or len(answer) > 65536:
        return Assessment(False, "FAIL", ("Respuesta vacia o supera el limite del evaluador.",))
    normalized = normalize(answer)
    if any(f" {normalize(term)} " in f" {normalized} " for term in rubric["forbidden_terms"]):
        return Assessment(False, "FAIL", ("Contiene una afirmacion/expresion prohibida anotada.",))
    if rubric["kind"] == "abstention":
        accepted = {normalize(text) for text in rubric["accepted_answers"]}
        # Recuperar material irrelevante no convierte una abstencion correcta
        # en una afirmacion falsa. Recuperacion y ACL tienen metricas separadas.
        passed = normalized in accepted and not _CITATION.search(answer)
        return Assessment(passed, "PASS" if passed else "FAIL",
                          () if passed else ("No coincide con una abstencion completa sin citas.",))

    statements = _statements(answer)
    if not statements or len(statements) > 128:
        return Assessment(False, "FAIL", ("Cantidad de enunciados fuera del alcance anotado.",))
    variants = {
        _statements(text)[0][0]: fact
        for fact in rubric["required_facts"] for text in fact["accepted_statements"]
    }
    allowed_sources = set(case["expected_source_ids"])
    retrieved = set(retrieved_sources)
    matched: set[str] = set()
    reasons: list[str] = []
    for statement, citations in statements:
        fact = variants.get(statement)
        if fact is None:
            reasons.append("Enunciado no respaldado por las variantes de referencia.")
            continue
        matched.add(fact["id"])
        if (not set(fact["source_ids"]) <= citations or not citations <= allowed_sources
                or not citations <= retrieved):
            reasons.append(f"Fuentes incorrectas/ausentes para el hecho {fact['id']}.")
    missing = {fact["id"] for fact in rubric["required_facts"]} - matched
    if missing:
        reasons.append("Faltan hechos obligatorios: " + ", ".join(sorted(missing)))
    return Assessment(not reasons, "FAIL" if reasons else "PASS", tuple(dict.fromkeys(reasons)),
                      tuple(sorted(matched)))


def partition_cases(cases: list[dict[str, Any]], *, seed: str, holdout_percent: int) -> None:
    """Agrupa documentos/preguntas antes del split; el hash no depende del orden."""
    if not seed or not 1 <= holdout_percent <= 99:
        raise ValueError("Split requiere seed y porcentaje holdout entre 1 y 99")
    ids: set[str] = set()
    source_groups: dict[str, str] = {}
    question_groups: dict[str, str] = {}
    for case in cases:
        group = case.get("evaluation_group")
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Cada caso requiere evaluation_group para separar tune/holdout")
        if case["id"] in ids:
            raise ValueError("ID de caso duplicado")
        ids.add(case["id"])
        keys = [(source_groups, sid.rsplit("#", 1)[0]) for sid in case.get("expected_source_ids", [])]
        keys.append((question_groups, normalize(case["question"])))
        for registry, key in keys:
            if key in registry and registry[key] != group:
                raise ValueError("Un documento/pregunta aparece en grupos distintos: fuga tune/holdout")
            registry[key] = group
        bucket = int.from_bytes(hashlib.sha256(f"{seed}\0{group}".encode()).digest()[:8], "big") % 100
        case["evaluation_split"] = "holdout" if bucket < holdout_percent else "tune"


def dataset_fingerprint(cases: list[dict[str, Any]]) -> str:
    payload = json.dumps(sorted(cases, key=lambda case: case["id"]), sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
