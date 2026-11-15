"""Immutable inputs and findings for a single matching evaluation snapshot."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


BusinessStatus = Literal["Rejected", "Held for Review", "Auto-Pay"]
RuleOutcome = Literal["PASS", "REJECT", "HOLD", "PENDING"]


@dataclass(frozen=True)
class MatchingConfig:
    price_tolerance: Decimal = Decimal("0.02")
    # Carried for future human-review authorization, not enforced by matching.
    analyst_approval_limit: Decimal = Decimal("5000")

    def __post_init__(self) -> None:
        for value in (self.price_tolerance, self.analyst_approval_limit):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("Configuration requires a finite nonnegative Decimal")


@dataclass(frozen=True)
class LineAssociation:
    """Caller-verified whole-line association, never parsed from raw extraction."""
    po_number: str
    po_line_number: int


@dataclass(frozen=True)
class InvoiceHistoryEntry:
    """Original invoice header from another case, supplied by Application.

    History completeness and excluding this case's versions are caller duties;
    this record neither records approval nor authorizes any payment.
    """
    supplier_id: str
    invoice_number: str
    invoice_date: date
    currency: str
    total_amount: Decimal


@dataclass(frozen=True)
class RuleResult:
    code: str
    outcome: RuleOutcome
    reason: str
    po_number: str | None = None
    line_index: int | None = None
    po_line_number: int | None = None


@dataclass(frozen=True)
class EvaluationResult:
    provisional_status: BusinessStatus
    final_status: BusinessStatus | None
    rules: tuple[RuleResult, ...]
    config: MatchingConfig

    @property
    def pending(self) -> tuple[RuleResult, ...]:
        return tuple(rule for rule in self.rules if rule.outcome == "PENDING")

    @property
    def complete(self) -> bool:
        return not self.pending
