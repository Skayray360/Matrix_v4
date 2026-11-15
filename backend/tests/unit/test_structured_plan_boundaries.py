# Creado por Aldo Garcia.
"""Limites y semantica del plan antes de compilar o abrir una fuente SQL."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common.errors import StructuredQueryRejectedError
from app.structured_data.adapters import QueryOutcome, ReadOnlySourceAdapter
from app.structured_data.schemas import QueryFilter, StructuredQueryPlan
from app.structured_data.sources import EntityConfig, SourceConfig
from app.structured_data.tool import StructuredDataTool
from app.structured_data.validator import QueryPolicyValidator
from tests.unit.test_structured_query import build_catalog, plan

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.mark.parametrize("value", [["x" * 513], float("nan"), float("inf"), [float("-inf")]])
def test_filter_values_enforce_limits_inside_and_outside_lists(value):
    with pytest.raises(ValidationError):
        QueryFilter(field="departamento", operator="in" if isinstance(value, list) else "eq", value=value)


@pytest.mark.parametrize("operator,value", [
    ("eq", ["MX"]), ("eq", None), ("in", "MX"), ("is_null", "MX"), ("is_not_null", []),
])
def test_operator_and_value_must_have_compatible_shapes(operator, value):
    with pytest.raises(ValidationError):
        QueryFilter(field="departamento", operator=operator, value=value)


def test_empty_in_and_null_checks_remain_supported():
    assert QueryFilter(field="departamento", operator="in", value=[]).value == []
    assert QueryFilter(field="departamento", operator="is_null").value is None


@pytest.mark.parametrize("field", ["aggregations", "sort"])
def test_plan_lists_cannot_generate_unbounded_sql(field):
    item = {"function": "count", "field": "*", "alias": "total"} if field == "aggregations" else {"field": "departamento"}
    with pytest.raises(ValidationError):
        plan(**{field: [item] * 31})


@pytest.mark.parametrize("overrides", [
    {"fields": ["departamento", "departamento"]},
    {"aggregations": [{"function": "count", "alias": "departamento"}]},
    {"aggregations": [{"function": "sum", "field": "*", "alias": "total"}]},
    {"fields": [f"field_{index}" for index in range(30)]},
])
def test_ambiguous_or_invalid_projections_are_rejected(overrides):
    with pytest.raises(ValidationError):
        plan(**overrides)


@pytest.mark.parametrize("make", [
    lambda: StructuredQueryPlan(source="rh_demo\n", entity="plantilla", fields=["departamento"]),
    lambda: EntityConfig(name="plantilla", table="plantilla\n"),
    lambda: SourceConfig(name="rh_demo\n", engine="mysql"),
])
def test_sql_identifiers_match_the_whole_value(make):
    with pytest.raises(ValidationError):
        make()


@pytest.mark.parametrize("overrides", [
    {"fields": ["departamento", "puesto"], "group_by": ["departamento"]},
    {"aggregations": [], "fields": ["departamento", "puesto"], "group_by": ["departamento"]},
    {"sort": [{"field": "puesto"}]},
])
def test_grouping_never_selects_or_orders_an_arbitrary_row(admin_context, overrides):
    validator = QueryPolicyValidator(catalog=build_catalog())
    with pytest.raises(StructuredQueryRejectedError, match="group_by"):
        validator.validate(plan(**overrides), ctx=admin_context)


def test_catalog_change_while_selecting_adapter_never_reaches_database(admin_context, monkeypatch):
    catalog = build_catalog()
    tool = StructuredDataTool(catalog=catalog)
    select_adapter = tool._adapter
    executions = []

    def replace_source_after_precheck(name):
        source = catalog.get(name)
        catalog.sources[name] = source.model_copy(update={"secret_ref": "OTHER_DATABASE_DSN"}, deep=True)
        return select_adapter(name)

    def record_execute(adapter, compiled, *, entity_name):
        executions.append(adapter.source.secret_ref)
        return QueryOutcome(columns=("departamento",), rows=(("otra_fuente",),), row_count=1,
                            truncated=False, source=adapter.source.name, entity=entity_name)

    monkeypatch.setattr(tool, "_adapter", replace_source_after_precheck)
    monkeypatch.setattr(ReadOnlySourceAdapter, "execute", record_execute)
    with pytest.raises(StructuredQueryRejectedError, match="politica.*cambio"):
        tool.run(plan(), ctx=admin_context)
    assert executions == []


def test_validated_contract_is_not_mutated_with_catalog(admin_context):
    catalog = build_catalog()
    validator = QueryPolicyValidator(catalog=catalog)
    validated = validator.validate(plan(), ctx=admin_context)
    catalog.get("rh_demo").entities[0].table = "otra_tabla"
    assert validated.entity.table == "demo_plantilla"


def test_adapter_keeps_configuration_snapshot_and_hides_parameter_values(monkeypatch):
    source = SourceConfig(name="demo", engine="sqlite", enabled=True, secret_ref="MATRIX_BOUNDARY_DSN")
    monkeypatch.setenv("MATRIX_BOUNDARY_DSN", "sqlite://")
    adapter = ReadOnlySourceAdapter(source)
    source.secret_ref = "OTHER_DATABASE_DSN"
    try:
        assert adapter.source.secret_ref == "MATRIX_BOUNDARY_DSN"
        assert adapter._get_engine().hide_parameters is True
    finally:
        adapter.dispose()
