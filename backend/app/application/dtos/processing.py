"""Durable processing contracts, separate from automatic matching outcomes."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.dtos.invoice_documents import DocumentMetadata, ExtractionResult


class ProcessingState(StrEnum):
    QUEUED = 'QUEUED'
    EXTRACTING = 'EXTRACTING'
    EXTRACTED = 'EXTRACTED'
    EVALUATING = 'EVALUATING'
    COMPLETED = 'COMPLETED'
    RETRY_WAIT = 'RETRY_WAIT'
    EXTRACTION_FAILED = 'EXTRACTION_FAILED'
    EVALUATION_INCOMPLETE = 'EVALUATION_INCOMPLETE'
    PROCESSING_FAILED = 'PROCESSING_FAILED'


@dataclass(frozen=True)
class ProcessingConfig:
    max_active: int = 1
    max_pending: int = 20
    token_limit: int = 10000
    window_seconds: int = 60
    lease_seconds: int = 120

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (
            self.max_active, self.max_pending, self.token_limit,
            self.window_seconds, self.lease_seconds)) or self.token_limit > 10000:
            raise ValueError('Invalid processing configuration')


@dataclass(frozen=True)
class ProcessingJob:
    invoice_id: UUID  # Stable upload case identity, not the raw invoices integer ID.
    job_id: UUID
    owner_id: int
    storage_key: str
    metadata: DocumentMetadata
    state: ProcessingState
    attempts: int
    next_eligible_at: datetime
    created_at: datetime
    updated_at: datetime
    error_code: str | None
    extraction: ExtractionResult | None
    raw_invoice_id: int | None
    evaluation_id: int | None
    attempt_history: tuple[dict, ...]
    events: tuple[dict, ...]
    document_validation: dict | None = None


@dataclass(frozen=True)
class ProcessingClaim:
    job: ProcessingJob
    token: UUID
    version: int
    expires_at: datetime
