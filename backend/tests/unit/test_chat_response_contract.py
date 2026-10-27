# Creado por Aldo Garcia.
"""Mismo corpus sintetico de contratos que ejecuta el cliente TypeScript."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.chat_schemas import (
    ChatCancellationResponse,
    ChatOperationResponse,
    ChatReplyResponse,
    validated_chat_response,
)
from app.api.routes.chat import router
from app.common.errors import MatrixError
from app.database.models import ChatOperation
from tests import test_chat_queue as queue_testing

pytestmark = pytest.mark.unit
queue_db = queue_testing.queue_db
controlled_queue = queue_testing.controlled_queue
CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/chat_contract.json").read_text())


@pytest.mark.parametrize("mutation", CONTRACT["invalid_mutations"], ids=lambda item: item["name"])
def test_invalid_operation_contract_is_rejected_without_sensitive_details(mutation):
    payload = deepcopy(CONTRACT["completed"])
    target = payload
    for key in mutation["path"][:-1]:
        target = target[key]
    target[mutation["path"][-1]] = mutation["value"]
    with pytest.raises(ValidationError):
        ChatOperationResponse.model_validate(payload)
    with pytest.raises(MatrixError) as caught:
        validated_chat_response(ChatOperationResponse, payload)
    public = caught.value.to_public_dict("synthetic-diagnostic")
    assert public["request_id"] == "synthetic-diagnostic"
    assert public["code"] == "internal_error"
    assert "Respuesta ficticia" not in str(public)
    assert "synthetic#0" not in str(public)


@pytest.mark.parametrize("state", ["queued", "running", "completed", "failed", "cancelled", "expired"])
def test_all_valid_states_preserve_the_existing_wire_contract(state):
    payload = deepcopy(CONTRACT["completed"])
    payload["status"] = state
    if state != "completed":
        payload["response"] = None
    if state == "failed":
        payload["error_code"] = "generation_failed"
    assert ChatOperationResponse.model_validate(payload).model_dump(exclude_unset=True) == payload


def test_optional_legacy_fields_and_safe_provenance_are_compatible():
    payload = deepcopy(CONTRACT["completed"])
    del payload["operation_id"]
    del payload["error_code"]
    del payload["response"]["answer_basis"]
    source = payload["response"]["sources"][0]
    source["provenance"] = {"document_sha256": "f" * 64, "embedding_dimension": 768}
    source["unapproved_internal_text"] = "SENSITIVE_SYNTHETIC_SOURCE"
    serialized = ChatOperationResponse.model_validate(payload).model_dump(exclude_unset=True)
    assert serialized["response"]["sources"][0]["provenance"] == source["provenance"]
    assert "operation_id" not in serialized
    assert "answer_basis" not in serialized["response"]
    assert "SENSITIVE_SYNTHETIC_SOURCE" not in str(serialized)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_source_scores_are_rejected(value):
    payload = deepcopy(CONTRACT["completed"]["response"])
    payload["sources"][0]["score"] = value
    with pytest.raises(ValidationError):
        ChatReplyResponse.model_validate(payload)


def test_operation_routes_publish_typed_responses_in_openapi():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    paths = app.openapi()["paths"]
    for path, method, code, model in (
        ("/api/chat", "post", "200", "ChatReplyResponse"),
        ("/api/chat/submit", "post", "202", "ChatOperationResponse"),
        ("/api/chat/requests/{client_request_id}", "get", "200", "ChatOperationResponse"),
        ("/api/chat/requests/{client_request_id}/cancel", "post", "200", "ChatCancellationResponse"),
    ):
        schema = paths[path][method]["responses"][code]["content"]["application/json"]["schema"]
        assert schema["$ref"] == f"#/components/schemas/{model}"


@pytest.mark.parametrize("payload", [{"ok": False}, {"ok": "true"}, {"ok": 1}, {}, []])
def test_cancellation_requires_a_true_boolean(payload):
    with pytest.raises(ValidationError):
        ChatCancellationResponse.model_validate(payload)


def test_corrupt_completed_operation_never_becomes_a_new_request(queue_db, controlled_queue, caplog):
    request_id = "synthetic-contract-request"
    with queue_testing.authenticated_client(queue_db) as owner:
        accepted = queue_testing.submit(owner, request_id)
        assert accepted.status_code == 202
        operation_id = owner.operation(request_id).id
        with queue_db() as db:
            operation = db.get(ChatOperation, operation_id)
            operation.status = "completed"
            operation.response = {"answer": "SENSITIVE_SYNTHETIC_REPLY"}
            db.commit()
        for response in (
            queue_testing.status(owner, request_id),
            queue_testing.submit(owner, request_id),
            owner.client.post("/api/chat", json={"message": "Hola", "client_request_id": request_id}),
        ):
            assert response.status_code == 500
            assert response.json()["code"] == "internal_error"
            assert "misma solicitud" in response.json()["message"]
            assert "SENSITIVE_SYNTHETIC_REPLY" not in response.text
        assert owner.operation(request_id).id == operation_id
        assert owner.operation(request_id).status == "completed"
        assert len(controlled_queue.launches) == 1
        assert controlled_queue.calls == []
    assert "SENSITIVE_SYNTHETIC_REPLY" not in caplog.text


def test_invalid_acceptance_envelope_keeps_the_committed_operation_recoverable(
    queue_db, controlled_queue, monkeypatch,
):
    original_submit = controlled_queue.manager.submit

    def invalid_envelope(db, operation):
        accepted = original_submit(db, operation)
        return {**accepted, "capacity": None}

    monkeypatch.setattr(controlled_queue.manager, "submit", invalid_envelope)
    request_id = "synthetic-acceptance-contract"
    with queue_testing.authenticated_client(queue_db) as owner:
        rejected_envelope = queue_testing.submit(owner, request_id)
        assert rejected_envelope.status_code == 500
        persisted = owner.operation(request_id)
        assert persisted.status == "running"
        recovered = queue_testing.status(owner, request_id)
        assert recovered.status_code == 200
        assert recovered.json()["operation_id"] == persisted.id
        duplicate = queue_testing.submit(owner, request_id)
        assert duplicate.status_code == 202
        assert duplicate.json()["operation_id"] == persisted.id
        assert len(controlled_queue.launches) == 1


def test_cancel_rejects_a_different_conversation_before_mutating_the_operation(queue_db, controlled_queue):
    request_id = "synthetic-cancel-context"
    with queue_testing.authenticated_client(queue_db) as owner:
        assert queue_testing.submit(owner, request_id).status_code == 202
        operation = owner.operation(request_id)
        url = f"/api/chat/requests/{request_id}/cancel"
        mismatch = owner.client.post(url, json={"conversation_id": "another-conversation"})
        assert mismatch.status_code == 404
        assert owner.operation(request_id).status == "running"
        assert owner.operation(request_id).finished_at is None
        cancelled = owner.client.post(url, json={"conversation_id": operation.conversation_id})
        assert cancelled.status_code == 200
        assert cancelled.json() == {"ok": True}
        assert owner.operation(request_id).status == "cancelled"
