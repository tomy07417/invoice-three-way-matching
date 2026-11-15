"""Rules use novel in-memory records, never the demonstration dataset."""
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext

import pytest

from backend.app.domain.entities import (
    GoodsReceivedNote, GoodsReceivedNoteLine, Invoice, InvoiceLine,
    PurchaseOrder, PurchaseOrderLine,
)
from backend.app.domain.matching.types import MatchingConfig, RuleResult
from backend.app.domain.matching import rules


@pytest.fixture
def invoice():
    return Invoice("Unfamiliar supplier", "RAW-TAX", "NOVEL-739", date(2026, 6, 2),
                   None, ("ORDER-Z",), "EUR", Decimal("100"), Decimal("0"),
                   Decimal("100"), (InvoiceLine("Unfamiliar fitting", Decimal("5"),
                   Decimal("100"), Decimal("500"), "raw text / not an integer"),))


@pytest.fixture
def po():
    return PurchaseOrder("ORDER-Z", "SUP-Z", date(2026, 6, 2), "EUR", "open",
                         (PurchaseOrderLine(7, "PART-Z", "Different description",
                                            Decimal("10"), Decimal("100")),))


@pytest.fixture
def grns():
    return (
        GoodsReceivedNote("R-A", "ORDER-Z", date(2026, 6, 2),
                          (GoodsReceivedNoteLine(7, Decimal("2")),)),
        GoodsReceivedNote("R-B", "ORDER-Z", date(2026, 6, 2),
                          (GoodsReceivedNoteLine(7, Decimal("3")),
                           GoodsReceivedNoteLine(8, Decimal("999")))),
        GoodsReceivedNote("R-C", "OTHER", date(2026, 6, 2),
                          (GoodsReceivedNoteLine(7, Decimal("999")),)),
    )


def assert_rule(result, code, outcome, reason, *, po_number=None, line_index=None):
    assert isinstance(result, RuleResult)
    assert (result.code, result.outcome, result.reason) == (code, outcome, reason)
    assert (result.po_number, result.line_index) == (po_number, line_index)


def test_missing_references(invoice):
    assert_rule(rules.check_po_references(invoice), "PO_REFERENCES", "PASS",
                "Invoice references at least one PO.")
    assert_rule(rules.check_po_references(replace(invoice, po_references=())),
                "PO_REFERENCES", "REJECT", "Invoice references no PO.")


def test_each_po_existence(po):
    assert_rule(rules.check_po_exists("ORDER-Z", po), "PO_EXISTS", "PASS",
                "Referenced PO exists.", po_number="ORDER-Z")
    assert_rule(rules.check_po_exists("MISSING", None), "PO_EXISTS", "REJECT",
                "Referenced PO does not exist.", po_number="MISSING")


@pytest.mark.parametrize("identity,outcome,reason", [
    ("SUP-Z", "PASS", "Resolved supplier matches PO supplier."),
    ("OTHER", "REJECT", "Resolved supplier differs from PO supplier."),
    (None, "PENDING", "Invoice supplier identity has not been resolved."),
])
def test_resolved_supplier(po, identity, outcome, reason):
    assert_rule(rules.check_supplier(identity, po), "SUPPLIER", outcome, reason,
                po_number=po.po_number)


@pytest.mark.parametrize("day,outcome", [(1, "REJECT"), (2, "PASS"), (3, "PASS")])
def test_invoice_date(invoice, po, day, outcome):
    result = rules.check_invoice_date(replace(invoice, invoice_date=date(2026, 6, day)), po)
    assert_rule(result, "INVOICE_DATE", outcome,
                "Invoice date precedes PO date." if outcome == "REJECT"
                else "Invoice date is on or after PO date.", po_number=po.po_number)


@pytest.mark.parametrize("quantity,outcome", [("5", "PASS"), ("5.0001", "HOLD"), ("4", "PASS")])
def test_quantity_sum_and_po_local_isolation(invoice, po, grns, quantity, outcome):
    line = replace(invoice.lines[0], quantity=Decimal(quantity))
    result = rules.check_quantity(line, po, po.lines[0], grns, 0)
    assert_rule(result, "QUANTITY", outcome,
                "Invoiced quantity exceeds available quantity." if outcome == "HOLD"
                else "Invoiced quantity is covered by available receipts.",
                po_number=po.po_number, line_index=0)
    assert result.po_line_number == 7
    assert rules.check_quantity(line, po, po.lines[0], (), 0).outcome == "HOLD"


@pytest.mark.parametrize("quantity,outcome", [
    ("6", "PASS"), ("6.0001", "HOLD"), ("7", "HOLD"),
])
def test_quantity_subtracts_approved_consumption(invoice, po, quantity, outcome):
    receipts = (GoodsReceivedNote(
        "RECEIVED-TEN", po.po_number, invoice.invoice_date,
        (GoodsReceivedNoteLine(7, Decimal("10")),),
    ),)
    line = replace(invoice.lines[0], quantity=Decimal(quantity))

    result = rules.check_quantity(
        line, po, po.lines[0], receipts, 2, approved_quantity=Decimal("4"),
    )

    assert result == RuleResult(
        "QUANTITY", outcome,
        "Invoiced quantity exceeds available quantity." if outcome == "HOLD"
        else "Invoiced quantity is covered by available receipts.",
        po.po_number, 2, 7,
    )


def test_quantity_default_is_zero_consumption(invoice, po, grns):
    expected = RuleResult(
        "QUANTITY", "PASS", "Invoiced quantity is covered by available receipts.",
        po.po_number, 0, 7,
    )
    assert rules.check_quantity(invoice.lines[0], po, po.lines[0], grns, 0) == expected
    assert rules.check_quantity(
        invoice.lines[0], po, po.lines[0], grns, 0, approved_quantity=Decimal("0"),
    ) == expected


@pytest.mark.parametrize("quantity,outcome", [("4", "PASS"), ("4.0001", "HOLD")])
def test_quantity_uses_all_received_grns_including_later_dates(
    invoice, po, grns, quantity, outcome,
):
    # Two matching receipts total five; unrelated PO/local-line quantities do not count.
    receipts = (grns[0], replace(grns[1], received_date=date(2026, 6, 3)), grns[2])
    assert receipts[1].received_date > invoice.invoice_date
    result = rules.check_quantity(
        replace(invoice.lines[0], quantity=Decimal(quantity)), po, po.lines[0],
        receipts, 0, approved_quantity=Decimal("1"),
    )
    assert result == RuleResult(
        "QUANTITY", outcome,
        "Invoiced quantity exceeds available quantity." if outcome == "HOLD"
        else "Invoiced quantity is covered by available receipts.",
        po.po_number, 0, 7,
    )


@pytest.mark.parametrize("quantity,outcome", [
    ("0.9999999999999999999999999999", "PASS"),
    ("1", "HOLD"),
])
def test_quantity_exact_subtraction_with_low_ambient_precision(invoice, po, quantity, outcome):
    receipts = (GoodsReceivedNote(
        "EXACT", po.po_number, invoice.invoice_date,
        (GoodsReceivedNoteLine(7, Decimal("1")),
         GoodsReceivedNoteLine(7, Decimal("0.0000000000000000000000000001"))),
    ),)
    with localcontext() as context:
        context.prec = 3
        result = rules.check_quantity(
            replace(invoice.lines[0], quantity=Decimal(quantity)), po, po.lines[0],
            receipts, 0, approved_quantity=Decimal("0.0000000000000000000000000002"),
        )
        assert context.prec == 3
    assert result == RuleResult(
        "QUANTITY", outcome,
        "Invoiced quantity exceeds available quantity." if outcome == "HOLD"
        else "Invoiced quantity is covered by available receipts.",
        po.po_number, 0, 7,
    )


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("source", ["invoice", "receipt", "approved"])
def test_quantity_nonfinite_evidence_is_pending(invoice, po, grns, value, source):
    line = invoice.lines[0]
    approved = Decimal("0")
    if source == "invoice":
        line = replace(line, quantity=Decimal(value))
    elif source == "receipt":
        grns = (replace(grns[0], lines=(GoodsReceivedNoteLine(7, Decimal(value)),)),)
    else:
        approved = Decimal(value)
    result = rules.check_quantity(
        line, po, po.lines[0], grns, 0, approved_quantity=approved,
    )
    assert result == RuleResult(
        "QUANTITY", "PENDING", "Quantity comparison requires finite values.",
        po.po_number, 0, 7,
    )


@pytest.mark.parametrize("approved", [None, 0, "0"])
def test_quantity_unsupported_approved_evidence_is_pending(invoice, po, grns, approved):
    result = rules.check_quantity(
        invoice.lines[0], po, po.lines[0], grns, 0, approved_quantity=approved,
    )
    assert result == RuleResult(
        "QUANTITY", "PENDING", "Quantity comparison requires finite values.",
        po.po_number, 0, 7,
    )


@pytest.mark.parametrize("price,outcome", [
    ("98", "PASS"), ("102", "PASS"), ("97.99999999999999999999", "HOLD"),
    ("102.00000000000000000001", "HOLD"), ("100", "PASS"),
])
def test_exact_price_boundaries(invoice, po, price, outcome):
    result = rules.check_price(replace(invoice.lines[0], unit_price=Decimal(price)),
                               po, po.lines[0], MatchingConfig(), 0)
    assert_rule(result, "UNIT_PRICE", outcome,
                "Unit price difference exceeds tolerance." if outcome == "HOLD"
                else "Unit price difference is within tolerance.",
                po_number=po.po_number, line_index=0)
    assert result.po_line_number == 7


def test_price_override_and_undefined_basis(invoice, po):
    line = replace(invoice.lines[0], unit_price=Decimal("103"))
    assert rules.check_price(line, po, po.lines[0], MatchingConfig(), 0).outcome == "HOLD"
    assert rules.check_price(line, po, po.lines[0], MatchingConfig(Decimal("0.03")), 0).outcome == "PASS"
    for reference in (Decimal("0"), Decimal("-100")):
        result = rules.check_price(line, po, replace(po.lines[0], unit_price=reference), MatchingConfig(), 0)
        assert_rule(result, "UNIT_PRICE", "PENDING", "Nonpositive PO price needs a percentage-basis policy.",
                    po_number=po.po_number, line_index=0)


@pytest.mark.parametrize("field", ["price_tolerance", "analyst_approval_limit"])
@pytest.mark.parametrize("value", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), 0.02])
def test_invalid_config(field, value):
    with pytest.raises(ValueError, match="finite nonnegative Decimal"):
        MatchingConfig(**{field: value})


def test_exact_price_comparison_is_independent_of_ambient_precision(invoice, po):
    with localcontext() as context:
        context.prec = 3
        line = replace(invoice.lines[0], unit_price=Decimal("102.0000000000000000000000000001"))
        assert rules.check_price(line, po, po.lines[0], MatchingConfig(), 0).outcome == "HOLD"
        assert context.prec == 3


def test_config_defaults_and_no_arbitrary_upper_bound():
    assert MatchingConfig().price_tolerance == Decimal("0.02")
    assert MatchingConfig().analyst_approval_limit == Decimal("5000")
    assert MatchingConfig(Decimal("2")).price_tolerance == Decimal("2")
