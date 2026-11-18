"""Pure review authorization; deliberately no database, HTTP, or model calls."""
from decimal import Decimal

import pytest

from app.domain.review import (
    DecisionAction,
    PolicyError,
    approval_eligibility,
    authorize_decision,
    normalize_comment,
)


ANALYST = "ap_analyst"
MANAGER = "ap_manager"


def assert_error(code, message, function, *args, **kwargs):
    with pytest.raises(PolicyError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    assert caught.value.message == message
    assert str(caught.value) == message


@pytest.mark.parametrize("comment", ["", " \n\t", None, 42, [], {}])
def test_comment_must_be_nonblank_text(comment):
    assert_error(
        "invalid_comment", "A nonblank comment is required.",
        normalize_comment, comment,
    )


def test_comment_trims_edges_and_preserves_content():
    assert normalize_comment(" \nReviewed PO\nwith supplier.\t ") == "Reviewed PO\nwith supplier."


@pytest.mark.parametrize("total", [Decimal("0.01"), Decimal("4999.999999999999999999"), Decimal("5000")])
def test_analyst_can_approve_positive_usd_through_inclusive_limit(total):
    assert approval_eligibility(ANALYST, total, "USD") == {
        "allowed": True, "requires_manager": False, "reason": None,
        "limit_amount": "5000", "limit_currency": "USD",
    }
    assert authorize_decision(ANALYST, "approve", total, "USD", " Checked ") == "Checked"


@pytest.mark.parametrize("total,currency,reason", [
    (Decimal("5000.000000000000000001"), "USD", "Approval above 5000 USD requires an AP Manager."),
    (Decimal("1"), "EUR", "Approval in EUR requires an AP Manager; no currency conversion is performed."),
    (Decimal("1"), "JPY", "Approval in JPY requires an AP Manager; no currency conversion is performed."),
])
def test_escalation_cannot_be_bypassed_by_analyst(total, currency, reason):
    assert approval_eligibility(ANALYST, total, currency) == {
        "allowed": False, "requires_manager": True, "reason": reason,
        "limit_amount": "5000", "limit_currency": "USD",
    }
    assert_error("approval_blocked", reason, authorize_decision,
                 ANALYST, "approve", total, currency, "Reviewed")
    assert approval_eligibility(MANAGER, total, currency) == {
        "allowed": True, "requires_manager": False, "reason": None,
        "limit_amount": "5000", "limit_currency": "USD",
    }
    assert authorize_decision(MANAGER, "approve", total, currency, " Escalated ") == "Escalated"


@pytest.mark.parametrize("role", [ANALYST, MANAGER])
@pytest.mark.parametrize("total", [None, Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("0"), Decimal("-1"), 1.0, "1"])
def test_invalid_total_blocks_even_manager(role, total):
    reason = "Approval requires a finite, positive Decimal invoice total."
    assert approval_eligibility(role, total, "USD") == {
        "allowed": False, "requires_manager": False, "reason": reason,
        "limit_amount": "5000", "limit_currency": "USD",
    }
    assert_error("approval_blocked", reason, authorize_decision,
                 role, "approve", total, "USD", "Reviewed")


@pytest.mark.parametrize("role", [ANALYST, MANAGER])
@pytest.mark.parametrize("currency", [None, "", "usd", "Usd", " USD", "US", "USDD", "123", "ÜSD", 123])
def test_currency_must_be_canonical_not_guessed(role, currency):
    reason = "Approval requires a three-letter uppercase currency code."
    assert approval_eligibility(role, Decimal("1"), currency) == {
        "allowed": False, "requires_manager": False, "reason": reason,
        "limit_amount": "5000", "limit_currency": "USD",
    }
    assert_error("approval_blocked", reason, authorize_decision,
                 role, "approve", Decimal("1"), currency, "Reviewed")


@pytest.mark.parametrize("role", ["intake", "AP Analyst", "AP Manager", "manager", "AP_MANAGER", " ap_analyst", "unknown", "", None, []])
def test_only_exact_persisted_ap_roles_can_decide(role):
    reason = "Only an AP Analyst or AP Manager may make review decisions."
    assert approval_eligibility(role, Decimal("1"), "USD") == {
        "allowed": False, "requires_manager": False, "reason": reason,
        "limit_amount": "5000", "limit_currency": "USD",
    }
    for action in DecisionAction:
        assert_error("forbidden_role", reason, authorize_decision,
                     role, action, Decimal("1"), "USD", "Reviewed")


@pytest.mark.parametrize("role", [ANALYST, MANAGER])
@pytest.mark.parametrize("action", ["approve", "reject", "request_credit_note"])
@pytest.mark.parametrize("comment", [" \n", None, 123])
def test_every_decision_requires_comment(role, action, comment):
    assert_error("invalid_comment", "A nonblank comment is required.",
                 authorize_decision, role, action, Decimal("1"), "USD", comment)


@pytest.mark.parametrize("role", [ANALYST, MANAGER])
@pytest.mark.parametrize("action", ["reject", "request_credit_note"])
@pytest.mark.parametrize("total,currency", [(None, None), (Decimal("9999999"), "EUR"), (Decimal("NaN"), "USD")])
def test_nonapproval_actions_are_not_amount_or_currency_gated(role, action, total, currency):
    assert authorize_decision(role, action, total, currency, " Reason ") == "Reason"


@pytest.mark.parametrize("action", ["Approve", "approve ", "override", "Auto-Pay", "", None, []])
def test_unsupported_actions_fail_explicitly(action):
    assert_error("unsupported_action", "Decision must be approve, reject, or request_credit_note.",
                 authorize_decision, ANALYST, action, Decimal("1"), "USD", "Reviewed")


def test_action_enum_has_only_the_three_human_actions():
    assert [action.value for action in DecisionAction] == ["approve", "reject", "request_credit_note"]


def test_custom_limit_uses_exact_decimal_comparison_and_display():
    limit = Decimal("1234.5600")
    assert approval_eligibility(ANALYST, limit, "USD", limit) == {
        "allowed": True, "requires_manager": False, "reason": None,
        "limit_amount": "1234.5600", "limit_currency": "USD",
    }
    assert authorize_decision(ANALYST, "approve", limit, "USD", "OK", limit) == "OK"
    reason = "Approval above 1234.5600 USD requires an AP Manager."
    assert_error("approval_blocked", reason, authorize_decision, ANALYST,
                 "approve", Decimal("1234.560000000000000001"), "USD", "OK", limit)


@pytest.mark.parametrize("limit", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 5000.0, None])
def test_invalid_limit_fails_closed(limit):
    assert_error("invalid_limit", "Analyst approval limit must be a finite, nonnegative Decimal.",
                 approval_eligibility, ANALYST, Decimal("1"), "USD", limit)


def test_zero_limit_escalates_any_positive_total():
    assert approval_eligibility(ANALYST, Decimal("0.01"), "USD", Decimal("0"))["requires_manager"] is True
