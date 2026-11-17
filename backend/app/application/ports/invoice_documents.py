"""Actual extraction/validation/storage boundaries, never payment authority."""
from typing import Protocol

from app.application.dtos.invoice_documents import ExtractionResult, ValidatedDocument


class DocumentValidationError(ValueError):
    """Safe code/message for a rejected upload; never file contents."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DocumentStorageError(RuntimeError):
    """Storage failed without leaking paths or original document contents."""


class ExtractionError(RuntimeError):
    """Sanitized attempt failure; the durable job processor owns retries.

    The adapter performs one request per call. Keeping retry scheduling outside
    the adapter lets RETRY_WAIT, attempts and restart recovery remain durable.
    Unknown token consumption must not be treated as zero usage.
    """

    def __init__(self, code: str, message: str, *, retryable: bool,
                 retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


class InvoiceExtractor(Protocol):
    async def extract(self, document: ValidatedDocument) -> ExtractionResult: ...


class DocumentValidator(Protocol):
    def validate(self, file_name: str, content_type: str, content: bytes) -> ValidatedDocument: ...


class DocumentStorage(Protocol):
    def put(self, document: ValidatedDocument) -> str: ...

    def read(self, storage_key: str) -> bytes: ...

    def delete(self, storage_key: str) -> None: ...
