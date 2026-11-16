"""One invoice-evaluation transaction; no approval or human-review write API."""
from contextlib import AbstractContextManager
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.application.dtos.invoice_evaluation import EvaluationRecord, RuleConfigurationSnapshot
from app.application.dtos.reference_data import ReferenceData
from app.domain.entities import Invoice
from app.domain.matching.types import InvoiceHistoryEntry, LineAssociation


class InvoiceNotFoundError(ValueError):
    """The requested existing invoice does not exist."""


class InvoiceConflictError(ValueError):
    """Evaluation cannot silently overwrite an existing invoice's raw data."""


class EvaluationConfigurationError(RuntimeError):
    """No applicable persisted configuration is available."""


class EvaluationPersistenceError(RuntimeError):
    """Safe adapter error without credentials or SQL/driver details."""


class InvoiceEvaluationTransaction(Protocol):
    def load_reference_data(self) -> ReferenceData: ...

    def register_invoice(self, invoice: Invoice, supplier_id: str | None,
                         invoice_id: int | None = None) -> int: ...

    def get_invoice(self, invoice_id: int) -> Invoice | None: ...

    def get_invoice_state(self, invoice_id: int) -> str | None: ...

    def get_configuration(self, at: datetime) -> RuleConfigurationSnapshot: ...

    def get_approved_quantities(self, po_numbers: tuple[str, ...]) -> dict[LineAssociation, Decimal]: ...

    def get_invoice_history(self, exclude_invoice_id: int) -> tuple[InvoiceHistoryEntry, ...] | None: ...

    def append_evaluation(self, record: EvaluationRecord) -> EvaluationRecord: ...

    def get_evaluation(self, evaluation_id: int) -> EvaluationRecord | None: ...

    def list_evaluations(self, invoice_id: int) -> tuple[EvaluationRecord, ...]: ...


class InvoiceEvaluationRepository(Protocol):
    def transaction(self) -> AbstractContextManager[InvoiceEvaluationTransaction]: ...
