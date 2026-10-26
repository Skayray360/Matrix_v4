# Creado por Aldo Garcia.
"""Procedencia de fixtures sinteticos conservada sin consultar el indice actual."""

from app.common.provenance import source_provenance
from app.memory.citations import citation_metadata
from app.rag.vector_store import payload_to_evidence


def payload():
    return {
        "source_id": "synthetic/manual.txt#0", "text": "Un ejemplo de prueba.",
        "document_id": "synthetic-document", "chunk_id": "synthetic-chunk",
        "filename": "manual.txt", "category": "tecnologia", "chunk_index": 0,
        "document_sha256": "a" * 64, "generation": "generation-A",
        "index_fingerprint": "b" * 64, "embedding_model": "local-synthetic:1",
        "embedding_dimension": 3, "ingestion_version": "synthetic-1",
    }


def test_retrieved_revision_survives_public_source_and_history_after_reindex():
    original = payload()
    public = payload_to_evidence(original, 0.8).to_public_dict()
    assert public["provenance"] == source_provenance(original)
    history = citation_metadata([public["source_id"]], [public])
    changed = dict(original, document_sha256="c" * 64, generation="generation-B")
    assert payload_to_evidence(changed, 0.8).to_public_dict()["provenance"] != history[0]["provenance"]
    assert history[0]["provenance"]["document_sha256"] == "a" * 64
    assert history[0]["provenance"]["generation"] == "generation-A"
    assert "text" not in history[0] and "document_id" not in history[0]


def test_legacy_sources_do_not_invent_revision_or_business_validity():
    public = payload_to_evidence({"source_id": "legacy.txt#0"}, 0.5).to_public_dict()
    assert "provenance" not in public
    assert citation_metadata(["legacy.txt#0"], None) == [{"source_id": "legacy.txt#0"}]
    assert not source_provenance({"effective_from": "2099-01-01", "approval_status": "approved"})


def test_history_preserves_only_cited_unambiguous_provenance():
    first = payload_to_evidence(payload(), 0.8).to_public_dict()
    second = payload_to_evidence(dict(payload(), generation="generation-B"), 0.8).to_public_dict()
    assert citation_metadata([first["source_id"]], [first, second]) == [{"source_id": first["source_id"]}]
    assert citation_metadata(["other#0"], [first]) == [{"source_id": "other#0"}]


def test_untrusted_extra_and_invalid_provenance_are_not_serialized():
    invalid = {"document_sha256": "unknown", "index_fingerprint": "x" * 64,
               "generation": "g\nheader", "embedding_model": "a" * 257,
               "embedding_dimension": True, "ingestion_version": None,
               "text": "private content", "owner_user_id": "other"}
    assert source_provenance(invalid) == {}
    public = payload_to_evidence(dict(payload(), **invalid), 0.5).to_public_dict()
    assert "provenance" not in public
