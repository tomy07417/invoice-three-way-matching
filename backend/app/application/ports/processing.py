"""Short-transaction durable job boundary; no connection escapes a method.

All timestamps are timezone-aware UTC. Workers must never use an expired claim.
`complete` invokes its callback inside a fenced transaction with the existing
invoice evaluation transaction adapter and case snapshot. Callback returns
(raw_invoice_id, evaluation_id, complete); it must do DB-only work (no network),
reuse register_invoice/append_evaluation, and must not commit the transaction.
The future orchestrator must use this hook, not independently commit an evaluation.
"""
from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID

from app.application.dtos.auth import Actor
from app.application.dtos.invoice_documents import DocumentMetadata, ExtractionResult
from app.application.dtos.processing import ProcessingClaim, ProcessingJob


class AdmissionRejected(Exception):
    """Bounded pending capacity exhausted; nothing was stored."""


class StaleLease(Exception):
    """Claim is expired, superseded or already consumed; nothing was stored."""


class ProcessingRepository(Protocol):
    def admit(self, actor: Actor, idempotency_key: str, storage_key: str,
              metadata: DocumentMetadata, estimated_tokens: int) -> ProcessingJob: ...
    def get(self, actor: Actor, invoice_id: UUID) -> ProcessingJob | None: ...
    def list(self, actor: Actor) -> tuple[ProcessingJob, ...]: ...
    def claim(self) -> ProcessingClaim | None: ...
    def extracted(self, claim: ProcessingClaim, result: ExtractionResult) -> None: ...
    def failed(self, claim: ProcessingClaim, code: str, *, retryable: bool,
               retry_after: datetime | None = None) -> None: ...
    def claim_evaluation(self) -> ProcessingClaim | None: ...
    def complete(self, claim: ProcessingClaim,
                 callback: Callable[..., tuple[int | None, int | None, bool]]) -> None: ...
