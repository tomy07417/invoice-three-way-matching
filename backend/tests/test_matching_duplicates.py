"""Duplicate rules consume immutable caller evidence, without storage or files."""
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest

from backend.app.domain.entities import Invoice
from backend.app.domain.matching.duplicates import (
    check_duplicate_history, normalize_invoice_number,
)
from backend.app.domain.matching.types import InvoiceHistoryEntry, RuleResult


@pytest.fixture
def invoice():
    return Invoice("Supplier", "RAW-TAX", " Inv-739 ", date(2026, 6, 2), None,
                   ("PO-Z",), "EUR", Decimal("100"), Decimal("0"),
                   Decimal("100"), ())


@pytest.fixture
def entry():
    return InvoiceHistoryEntry("SUP-Z", "OTHER-1", date(2026, 6, 2), "EUR",
                               Decimal("100"))


def findings(duplicate="PASS", similar="PASS"):
    duplicate_reasons = {
        "PASS": "No same-supplier normalized invoice number found in history.",
        "REJECT": "Same supplier and normalized invoice number found in history.",
        "PENDING": "Duplicate comparison requires usable invoice numbers in history.",
    }
    similar_reasons = {
        "PASS": "No different-number same-supplier same-currency invoice with identical total and date found in history.",
        "HOLD": "Different invoice number with identical total and date found for the same supplier and currency.",
        "PENDING": "Similarity comparison requires usable invoice numbers, currency, date and finite totals.",
    }
    return (RuleResult("DUPLICATE", duplicate, duplicate_reasons[duplicate]),
            RuleResult("SIMILAR_INVOICE", similar, similar_reasons[similar]))


@pytest.mark.parametrize("raw,expected", [
    (" InV - 739\t\n", "inv739"), ("INV\u00a0-\u2003739", "inv739"),
    ("Straße-1", "strasse1"), ("INV_739", "inv_739"),
    ("INV/739", "inv/739"), ("INV–739", "inv–739"),
    ("é-1", "é1"), ("e\u0301-1", "e\u03011"), (" -\t", ""),
])
def test_normalization_only_approved_characters(raw, expected):
    assert normalize_invoice_number(raw) == expected


@pytest.mark.parametrize("changes", [
    {}, {"total_amount": Decimal("999")}, {"currency": "USD"},
    {"invoice_date": date(2020, 1, 1)},
    {"total_amount": Decimal("999"), "currency": "USD",
     "invoice_date": date(2020, 1, 1)},
])
def test_same_number_rejects_regardless_of_other_header_values(invoice, entry, changes):
    previous = replace(entry, invoice_number="i n v - 7 3 9", **changes)
    assert check_duplicate_history(invoice, "SUP-Z", (previous,)) == findings("REJECT")


def test_other_supplier_does_not_trigger_either_rule(invoice, entry):
    history = (replace(entry, supplier_id="OTHER", invoice_number="INV739"),
               replace(entry, supplier_id="OTHER"))
    assert check_duplicate_history(invoice, "SUP-Z", history) == findings()


def test_different_number_equal_total_date_holds_never_rejects(invoice, entry):
    assert check_duplicate_history(invoice, "SUP-Z", (entry,)) == findings(similar="HOLD")


@pytest.mark.parametrize("changes", [
    {"invoice_date": date(2026, 6, 3)}, {"total_amount": Decimal("100.01")},
    {"currency": "USD"},
])
def test_changed_date_amount_or_currency_is_not_similar(invoice, entry, changes):
    assert check_duplicate_history(invoice, "SUP-Z", (replace(entry, **changes),)) == findings()


@pytest.mark.parametrize("amount,similar", [
    ("100.0000", "HOLD"), ("100.0000000000000000000000000001", "PASS"),
])
def test_decimal_equality_is_exact_and_not_rounded(invoice, entry, amount, similar):
    with localcontext() as context:
        context.prec = 3
        assert check_duplicate_history(invoice, "SUP-Z", (
            replace(entry, total_amount=Decimal(amount)),)) == findings(similar=similar)
        assert context.prec == 3


def test_both_findings_survive_and_history_order_does_not_matter(invoice, entry):
    history = (entry, replace(entry, invoice_number="INV739"))
    expected = findings("REJECT", "HOLD")
    assert check_duplicate_history(invoice, "SUP-Z", history) == expected
    assert check_duplicate_history(invoice, "SUP-Z", history[::-1]) == expected


def test_empty_history_is_authoritative(invoice):
    assert check_duplicate_history(invoice, "SUP-Z", ()) == findings()


@pytest.mark.parametrize("supplier,history,reason", [
    ("SUP-Z", None, "Invoice history has not been supplied."),
    (None, (), "Invoice supplier identity has not been resolved."),
    ("", (), "Invoice supplier identity has not been resolved."),
    (" \t", (), "Invoice supplier identity has not been resolved."),
])
def test_missing_mandatory_evidence_is_explicit(invoice, supplier, history, reason):
    assert check_duplicate_history(invoice, supplier, history) == (
        RuleResult("DUPLICATE", "PENDING", reason),
        RuleResult("SIMILAR_INVOICE", "PENDING", reason),
    )


@pytest.mark.parametrize("number", ["", " - \n", "---"])
def test_unusable_current_number_never_passes_or_collides(invoice, entry, number):
    current = replace(invoice, invoice_number=number)
    expected = tuple(RuleResult(code, "PENDING", "Invoice number is unusable after normalization.")
                     for code in ("DUPLICATE", "SIMILAR_INVOICE"))
    assert check_duplicate_history(current, "SUP-Z", ()) == expected
    assert check_duplicate_history(current, "SUP-Z", (
        replace(entry, invoice_number=number),)) == expected


def test_unusable_history_number_does_not_clear_or_create_duplicate(invoice, entry):
    assert check_duplicate_history(invoice, "SUP-Z", (
        replace(entry, invoice_number=" -- "),)) == findings("PENDING", "PENDING")
    # Unusable records from other suppliers cannot undermine this supplier's evidence.
    assert check_duplicate_history(invoice, "SUP-Z", (
        replace(entry, supplier_id="OTHER", invoice_number=" -- "),)) == findings()


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_nonfinite_totals_leave_similarity_pending_but_do_not_block_duplicate(invoice, entry, amount):
    invalid = replace(entry, total_amount=Decimal(amount))
    assert check_duplicate_history(invoice, "SUP-Z", (invalid,)) == findings(similar="PENDING")
    assert check_duplicate_history(invoice, "SUP-Z", (
        replace(invalid, invoice_number="INV739"),)) == findings("REJECT")
    assert check_duplicate_history(replace(invoice, total_amount=Decimal(amount)),
                                   "SUP-Z", (entry,)) == findings(similar="PENDING")


def test_positive_findings_survive_other_unusable_records(invoice, entry):
    history = (replace(entry, invoice_number=" -- "), entry,
               replace(entry, invoice_number="INV739"))
    assert check_duplicate_history(invoice, "SUP-Z", history) == findings("REJECT", "HOLD")


@pytest.mark.parametrize("changes", [{"currency": ""}, {"invoice_date": None}])
def test_missing_similarity_evidence_stays_explicit(invoice, entry, changes):
    assert check_duplicate_history(replace(invoice, **changes), "SUP-Z", (entry,)) == findings(similar="PENDING")
    assert check_duplicate_history(invoice, "SUP-Z", (replace(entry, **changes),)) == findings(similar="PENDING")


def test_original_records_and_text_are_preserved(invoice, entry):
    original = replace(entry, invoice_number=" inv - 739 ")
    history = (original, entry)
    before = (replace(invoice), tuple(replace(item) for item in history))
    assert check_duplicate_history(invoice, "SUP-Z", history) == findings("REJECT", "HOLD")
    assert (invoice, history) == before
    assert invoice.invoice_number == " Inv-739 "
    assert original.invoice_number == " inv - 739 "
