# Creado por Aldo Garcia.
"""La politica declarativa falla cerrado ante typos y declaraciones ambiguas."""

from types import SimpleNamespace

import pytest

from app.authorization import categories
from app.authorization.policy import PolicyEngine
from app.common.errors import ConfigurationError
from tests.conftest import make_context

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    path = tmp_path / "categories.yaml"
    monkeypatch.setattr(categories, "get_settings", lambda: SimpleNamespace(
        authorization_policy_path=path, knowledge_root_path=tmp_path / "documents",
    ))
    categories._cached_registry.cache_clear()
    yield path
    categories._cached_registry.cache_clear()


@pytest.mark.parametrize("text", [
    "categories:\n  - name: nomina_confidencial\n    wildcard_eligble: false\n",
    "default_wildcard_eligble: false\ncategories: []\n",
    "categories:\n  - name: nomina\n    wildcard_eligible: false\n  - name: nomina\n    wildcard_eligible: true\n",
    "categories:\n  - name: nomina\n    wildcard_eligible: false\n    wildcard_eligible: true\n",
    "default_wildcard_eligible: false\ndefault_wildcard_eligible: true\n",
    "default_sensitivity: unknowable\n",
    "version: 2\n",
    'categories:\n  - name: "nomina\\n"\n',
    "categories: !!python/object/apply:os.system ['echo invalid']\n",
])
def test_invalid_policy_never_publishes_grants(policy_file, text):
    policy_file.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        categories.refresh_registry()
    with pytest.raises(ConfigurationError):
        PolicyEngine().effective_categories(make_context())


def test_invalid_replacement_revokes_cached_policy_instead_of_reusing_it(policy_file):
    policy_file.write_text("categories:\n  - name: nomina_confidencial\n    wildcard_eligible: true\n", encoding="utf-8")
    categories.refresh_registry()
    held_engine = PolicyEngine()
    assert held_engine.can_read_category(make_context(), "nomina_confidencial").allowed
    policy_file.write_text("categories:\n  - name: nomina_confidencial\n    wildcard_eligble: false\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        categories.refresh_registry()
    with pytest.raises(ConfigurationError):
        held_engine.can_read_category(make_context(), "nomina_confidencial")


def test_valid_policy_preserves_nominal_and_wildcard_access(policy_file):
    policy_file.write_text(
        "version: 1\ndefault_sensitivity: internal\ndefault_wildcard_eligible: false\n"
        "categories:\n  - name: nomina_confidencial\n    sensitivity: restricted\n    wildcard_eligible: false\n"
        "  - name: prestaciones\n    sensitivity: internal\n    wildcard_eligible: true\n",
        encoding="utf-8",
    )
    categories.refresh_registry()
    engine = PolicyEngine()
    admin = make_context()
    nominal = make_context(categories=frozenset({"nomina_confidencial"}), wildcard=False)
    assert not engine.can_read_category(admin, "nomina_confidencial").allowed
    assert engine.can_read_category(admin, "prestaciones").allowed
    assert engine.can_read_category(nominal, "nomina_confidencial").allowed
