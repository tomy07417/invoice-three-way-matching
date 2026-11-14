"""Immutable inputs and findings for a single matching evaluation snapshot."""
from dataclasses import dataclass
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
class PolicyFacts:
    """Verified facts for this invoice snapshot, not switches to omit checks.

    Clearance means an applicable externally established policy was evaluated.
    False clearance leaves the check pending; it does not invent a discrepancy.
    No duplicate criteria or policy resolution algorithm is implemented here.
    """
    duplicate: bool | None = None
    quantities_comparable: bool = False
    unit_prices_comparable: bool = False
    supplier_activity_cleared: bool = False
    po_status_cleared: bool = False

    def __post_init__(self) -> None:
        if self.duplicate is not None and type(self.duplicate) is not bool:
            raise ValueError("Duplicate fact must be bool or None")
        for value in (self.quantities_comparable, self.unit_prices_comparable,
                      self.supplier_activity_cleared, self.po_status_cleared):
            if type(value) is not bool:
                raise ValueError("Policy clearance facts must be bool")


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
