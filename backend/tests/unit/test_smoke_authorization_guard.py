# Creado por Aldo Garcia.
"""El smoke con escrituras exige base descartable y no imprime evidencias."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import smoke_authorization as smoke

pytestmark = [pytest.mark.unit, pytest.mark.security]


def test_smoke_checks_disposable_environment_before_loading_runtime(monkeypatch):
    initialized = []

    def reject_environment():
        raise ValueError("not disposable")

    monkeypatch.setattr(smoke, "require_disposable_database", reject_environment)
    monkeypatch.setattr(smoke, "Orchestrator", lambda: initialized.append(True))
    with pytest.raises(ValueError, match="not disposable"):
        smoke.main()
    assert initialized == []


@pytest.mark.parametrize("change", [
    {"is_active": False}, {"is_synthetic_test": False}, {"auth_source": "entra"},
])
def test_smoke_does_not_impersonate_real_or_inactive_accounts(change):
    user = SimpleNamespace(**({"is_active": True, "is_synthetic_test": True, "auth_source": "local_test"} | change))
    db = SimpleNamespace(execute=lambda statement: SimpleNamespace(scalar_one=lambda: user))
    with pytest.raises(ValueError, match="sinteticas"):
        smoke._context(db, "Matrix")


def test_smoke_leak_report_keeps_document_text_out_of_output(monkeypatch, capsys):
    @contextmanager
    def session_scope():
        yield object()

    private_text = "SYNTHETIC_PRIVATE_DOCUMENT_CONTENT"
    outcome = SimpleNamespace(evidences=[SimpleNamespace(category="nomina", text=private_text)], model="synthetic")
    monkeypatch.setattr(smoke, "require_disposable_database", lambda: None)
    monkeypatch.setattr(smoke, "session_scope", session_scope)
    monkeypatch.setattr(smoke, "_context", lambda *args: object())
    monkeypatch.setattr(smoke, "get_policy_engine", lambda: SimpleNamespace(effective_categories=lambda ctx: {"prestaciones"}))
    monkeypatch.setattr(smoke, "Orchestrator", lambda: SimpleNamespace(handle_chat=lambda *args, **kwargs: outcome))
    monkeypatch.setattr(smoke, "MemoryService", lambda: SimpleNamespace(create_conversation=lambda *args, **kwargs: object()))
    assert smoke.main() == 1
    output = capsys.readouterr().out
    assert "categorias ajenas" in output
    assert private_text not in output
