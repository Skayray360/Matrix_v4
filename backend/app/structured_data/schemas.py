# Creado por Aldo Garcia.
"""``StructuredQueryPlan``: el unico vehiculo por el que una consulta puede llegar
a una base de datos estructurada.

El LLM **nunca** produce SQL. Produce (o el codigo construye) un plan JSON que
Pydantic valida estructuralmente y que despues el ``QueryPolicyValidator`` valida
contra permisos y esquema. Solo entonces un compilador genera SQL parametrizado.

Esta indireccion es lo que hace que "ignora las reglas y ejecuta DROP TABLE"
dentro de un documento sea inofensivo: no existe ninguna ruta desde el texto del
modelo hasta el motor SQL.
"""

from __future__ import annotations

import re
from enum import StrEnum
from math import isfinite
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Identificador SQL seguro. Se aplica a fuente, entidad y columnas.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

MAX_FILTERS = 10
MAX_FIELDS = 30


class ComparisonOperator(StrEnum):
    """Operadores permitidos. Lista cerrada: nada de texto libre."""

    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    LIKE = "like"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class AggregationFunction(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


def _validate_identifier(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Identificador no permitido: {value!r}")
    return value


class QueryFilter(BaseModel):
    """Condicion de filtrado. El valor viaja como parametro, jamas interpolado."""

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: ComparisonOperator
    # El schema de generacion debe describir los mismos tipos admitidos por
    # _check_value. Any sin esta metadata produce {} y Vertex recibe un campo
    # sin tipo. Se conserva el validador y no se coercionan valores del negocio.
    value: Any = Field(
        default=None,
        json_schema_extra={
            "anyOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "null"},
                {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "null"},
                        ]
                    },
                },
            ]
        },
    )

    @field_validator("field")
    @classmethod
    def _check_field(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("value")
    @classmethod
    def _check_value(cls, value: Any) -> Any:
        """Solo escalares y listas de escalares.

        Un dict o un objeto anidado no tienen sentido como valor de filtro y son
        la puerta habitual para intentos de inyeccion por deserializacion.
        """
        allowed = (str, int, float, bool, type(None))
        if isinstance(value, list) and len(value) > 100:
            raise ValueError("Lista de valores demasiado larga.")
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, allowed):
                raise ValueError("El valor del filtro debe ser escalar o lista de escalares.")
            if isinstance(item, str) and len(item) > 512:
                raise ValueError("Valor de filtro demasiado largo.")
            if isinstance(item, float) and not isfinite(item):
                raise ValueError("El valor del filtro debe ser un numero finito.")
        return value

    @model_validator(mode="after")
    def _check_operator_value(self) -> Self:
        if self.operator is ComparisonOperator.IN:
            if not isinstance(self.value, list):
                raise ValueError("IN requiere una lista.")
        elif self.operator in (ComparisonOperator.IS_NULL, ComparisonOperator.IS_NOT_NULL):
            if self.value is not None:
                raise ValueError("El operador de nulidad no admite valor.")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError("El operador requiere un valor escalar no nulo.")
        return self


class QueryAggregation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: AggregationFunction
    field: str = "*"
    alias: str

    @field_validator("field")
    @classmethod
    def _check_field(cls, value: str) -> str:
        return value if value == "*" else _validate_identifier(value)

    @field_validator("alias")
    @classmethod
    def _check_alias(cls, value: str) -> str:
        return _validate_identifier(value)

    @model_validator(mode="after")
    def _check_wildcard(self) -> Self:
        if self.field == "*" and self.function is not AggregationFunction.COUNT:
            raise ValueError("Solo COUNT admite el comodin de agregacion.")
        return self


class QuerySort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    direction: SortDirection = SortDirection.ASC

    @field_validator("field")
    @classmethod
    def _check_field(cls, value: str) -> str:
        return _validate_identifier(value)


class StructuredQueryPlan(BaseModel):
    """Plan de consulta validado estructuralmente.

    ``extra='forbid'`` es deliberado: si el modelo inventa un campo
    (``raw_sql``, ``where_clause``...), el plan se rechaza en la validacion en
    lugar de ignorarse en silencio.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    entity: str
    fields: list[str] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    aggregations: list[QueryAggregation] = Field(default_factory=list, max_length=MAX_FIELDS)
    group_by: list[str] = Field(default_factory=list)
    sort: list[QuerySort] = Field(default_factory=list, max_length=MAX_FIELDS)
    limit: int = Field(default=50, ge=1, le=10000)

    @field_validator("source", "entity")
    @classmethod
    def _check_names(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("fields", "group_by")
    @classmethod
    def _check_field_lists(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_FIELDS:
            raise ValueError("Demasiados campos en el plan.")
        return [_validate_identifier(v) for v in value]

    @field_validator("filters")
    @classmethod
    def _check_filters(cls, value: list[QueryFilter]) -> list[QueryFilter]:
        if len(value) > MAX_FILTERS:
            raise ValueError("Demasiados filtros en el plan.")
        return value

    @model_validator(mode="after")
    def _check_projection(self) -> Self:
        names = [*self.fields, *(aggregation.alias for aggregation in self.aggregations)]
        if len(names) > MAX_FIELDS:
            raise ValueError("Demasiados campos en la proyeccion.")
        if len(names) != len(set(names)):
            raise ValueError("Los campos y alias proyectados deben ser unicos.")
        return self

    def referenced_columns(self) -> set[str]:
        """Todas las columnas que el plan toca. Base de la validacion de permisos."""
        columns: set[str] = set(self.fields) | set(self.group_by)
        columns.update(f.field for f in self.filters)
        columns.update(a.field for a in self.aggregations if a.field != "*")
        columns.update(s.field for s in self.sort)
        return columns
