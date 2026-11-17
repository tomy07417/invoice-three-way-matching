"""Immutable processing inputs preserve missing facts and private ownership."""
from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from app.application.dtos.auth import Actor, Role
from app.application.dtos.invoice_documents import DocumentMetadata, TokenUsage, ValidatedDocument


def test_document_content_matches_metadata_and_is_frozen():
    content = b"synthetic"
    metadata = DocumentMetadata("sample.pdf", "application/pdf", len(content), sha256(content).hexdigest(), 1)
    document = ValidatedDocument(content, metadata)
    with pytest.raises(FrozenInstanceError):
        document.content = b"changed"
    with pytest.raises(ValueError, match="content does not match metadata"):
        ValidatedDocument(b"different", metadata)
    assert document.content == content and document.metadata == metadata


@pytest.mark.parametrize("input_tokens,output_tokens", [(True, 1), (-1, 0), (0, -1), (1, 2.5)])
def test_token_usage_rejects_invalid_counters(input_tokens, output_tokens):
    with pytest.raises(ValueError, match="nonnegative integers"):
        TokenUsage(input_tokens, output_tokens)


def test_token_usage_total_is_exact():
    assert TokenUsage(9, 12).total_tokens == 21


def test_intake_visibility_does_not_depend_on_client_role_claims():
    intake = Actor(1, "intake", Role.INTAKE)
    assert intake.can_view(1) and not intake.can_view(2)
    for role in (Role.AP_ANALYST, Role.AP_MANAGER):
        assert Actor(2, "reviewer", role).can_view(1)
    with pytest.raises(FrozenInstanceError):
        intake.role = Role.AP_MANAGER
