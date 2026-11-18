"""Structured evaluation inputs and stored evidence, independent of adapters."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from app.domain.entities import Invoice
from app.domain.matching.types import EvaluationResult, LineAssociation, MatchingConfig


@dataclass(frozen=True)
class EvaluateInvoiceCommand:
    invoice: Invoice
    actor_id: str
    supplier_id: str | None = None
    invoice_id: int | None = None
    associations: Mapping[int, LineAssociation | None] | None = None
    net_unit_prices: Mapping[int, Decimal] | None = None
    origin: str = "structured-input"
    association_methods: Mapping[int, str] | None = None

    def __post_init__(self) -> None:
        for value in (self.actor_id, self.origin):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Evaluation requires a nonempty actor and origin")
        if self.invoice_id is not None and (type(self.invoice_id) is not int or self.invoice_id <= 0):
            raise ValueError("Existing invoice ID must be a positive integer")
        if self.supplier_id is not None and (not isinstance(self.supplier_id, str) or not self.supplier_id.strip()):
            raise ValueError("Supplier ID must be nonempty when supplied")
        for name in ("associations", "net_unit_prices", "association_methods"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))


@dataclass(frozen=True)
class RuleConfigurationSnapshot:
    configuration_id: int
    effective_from: datetime
    config: MatchingConfig


@dataclass(frozen=True)
class EvaluationRecord:
    """An unsaved record has no evaluation_id; append returns a persisted copy.

    JSON-compatible snapshots contain original input and the actual facts used.
    They are evidence, never a human decision or proof of payment authorization.
    """
    invoice_id: int
    configuration_id: int
    evaluated_at: datetime
    actor_id: str
    origin: str
    result: EvaluationResult
    input_snapshot: dict
    context_snapshot: dict
    evaluation_id: int | None = None

    @property
    def operational_state(self) -> str:
        return "evaluated" if self.result.complete else "evaluation_incomplete"
