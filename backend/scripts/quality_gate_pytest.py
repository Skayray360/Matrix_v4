# Creado por Aldo Garcia.
"""Partition root collection by real service dependencies, only for the gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

SERVICE_FIXTURES = frozenset({
    "db_session", "require_database", "database_available",
    "require_ollama", "ollama_available", "require_qdrant", "qdrant_available",
})


def test_group(item: pytest.Item) -> str:
    if SERVICE_FIXTURES.intersection(item.fixturenames) or any(
        item.get_closest_marker(marker) is not None for marker in ("integration", "ollama")
    ):
        return "integration_services"
    return "standalone"


def pytest_addoption(parser):
    parser.addoption("--matrix-test-group", choices=("standalone", "integration_services"), required=True)
    parser.addoption("--matrix-collection-report", required=True)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # Run before the repository's disposable-DB guard: standalone must never
    # probe the database just because another collected group depends on it.
    group = config.getoption("--matrix-test-group")
    selected, deselected = [], []
    counts = {"standalone": 0, "integration_services": 0}
    for item in items:
        actual = test_group(item)
        counts[actual] += 1
        (selected if actual == group else deselected).append(item)
    inventory = {
        "group": group,
        "collected_total": len(items),
        "groups": counts,
        "selected": len(selected),
        "nodeids_sha256": hashlib.sha256("\n".join(sorted(item.nodeid for item in items)).encode()).hexdigest(),
    }
    Path(config.getoption("--matrix-collection-report")).write_text(json.dumps(inventory), encoding="utf-8")
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
