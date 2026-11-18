"""Pure human-review permissions, not matching outcomes or payment authorization.

The caller must supply a server-resolved role and enforce current, complete
assessment evidence, quantity availability, and payment deduplication in its
transaction. Permission here does not override any of those service guards.
"""
from decimal import Decimal
from enum import StrEnum
import re
from typing import TypedDict


# Persisted session role values. Keep Domain independent of Application DTOs.
_AP_ROLES = ("ap_analyst", "ap_manager")
_ROLE_REASON = "Only an AP Analyst or AP Manager may make review decisions."


class DecisionAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CREDIT_NOTE = "request_credit_note"


class PolicyError(ValueError):
    """Stable machine-readable code and human-readable reason for rejection."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ApprovalEligibility(TypedDict):
    allowed: bool
    requires_manager: bool
    reason: str | None
    limit_amount: str
    limit_currency: str


def normalize_comment(value: str) -> str:
    """Trim outer whitespace without rewriting the reviewer's explanation."""
    if not isinstance(value, str) or not value.strip():
        raise PolicyError("invalid_comment", "A nonblank comment is required.")
    return value.strip()


def approval_eligibility(
    role: str,
    total: Decimal | None,
    currency: str | None,
    analyst_limit: Decimal = Decimal("5000"),
) -> ApprovalEligibility:
    """Return amount/currency permission and the USD escalation route.

    Only canonical uppercase ASCII currency codes are accepted. No FX or
    float conversion is performed. A Manager still needs a valid positive
    total and currency; escalation cannot repair missing or malformed data.
    """
    if (
        not isinstance(analyst_limit, Decimal)
        or not analyst_limit.is_finite()
        or analyst_limit < 0
    ):
        raise PolicyError(
            "invalid_limit", "Analyst approval limit must be a finite, nonnegative Decimal."
        )

    result: ApprovalEligibility = {
        "allowed": False,
        "requires_manager": False,
        "reason": None,
        "limit_amount": str(analyst_limit),
        "limit_currency": "USD",
    }
    if role not in _AP_ROLES:
        result["reason"] = _ROLE_REASON
    elif not isinstance(total, Decimal) or not total.is_finite() or total <= 0:
        result["reason"] = "Approval requires a finite, positive Decimal invoice total."
    elif not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
        result["reason"] = "Approval requires a three-letter uppercase currency code."
    elif role == "ap_manager":
        result["allowed"] = True
    elif currency != "USD":
        result["requires_manager"] = True
        result["reason"] = (
            f"Approval in {currency} requires an AP Manager; no currency conversion is performed."
        )
    elif total > analyst_limit:
        result["requires_manager"] = True
        result["reason"] = f"Approval above {analyst_limit} USD requires an AP Manager."
    else:
        result["allowed"] = True
    return result


def authorize_decision(
    role: str,
    action: str,
    total: Decimal | None,
    currency: str | None,
    comment: str,
    analyst_limit: Decimal = Decimal("5000"),
) -> str:
    """Validate a human action and return its normalized mandatory comment.

    Rejection and credit-note requests require AP permission and a comment,
    but are not approval-limit gated. This function changes no case state;
    the service owns credit-note payment blocks and append-only audit writes.
    """
    if role not in _AP_ROLES:
        raise PolicyError("forbidden_role", _ROLE_REASON)
    try:
        decision = DecisionAction(action)
    except (ValueError, TypeError):
        raise PolicyError(
            "unsupported_action", "Decision must be approve, reject, or request_credit_note."
        ) from None
    normalized_comment = normalize_comment(comment)
    if decision == DecisionAction.APPROVE:
        eligibility = approval_eligibility(role, total, currency, analyst_limit)
        if not eligibility["allowed"]:
            raise PolicyError("approval_blocked", eligibility["reason"] or "Approval is not allowed.")
    return normalized_comment
