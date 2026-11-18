"""Public projection contract: evaluation-time evidence, never live availability."""
from copy import deepcopy
from decimal import localcontext
import json

import pytest

from app.application.dtos.review_comparison import comparison_rows
from app.domain.matching.types import RuleResult


def snapshots():
    invoice = {
        "invoice": {
            "currency": "USD", "po_references": ["PO-A", "PO-B"],
            "lines": [
                {"line_index": 0, "description": "First", "quantity": "2.00",
                 "unit_price": "999", "po_line_reference": "1"},
                {"line_index": 1, "description": "Second", "quantity": "3.0",
                 "unit_price": "999", "po_line_reference": "1"},
            ],
        },
        "associations": [
            {"line_index": 0, "po_number": "PO-A", "po_line_number": 1},
            {"line_index": 1, "po_number": "PO-B", "po_line_number": 1},
        ],
        "net_unit_prices": [{"line_index": 0, "net_unit_price": "10.0100"}],
    }
    context = {
        "purchase_orders": [
            {"po_number": "PO-A", "currency": "USD", "lines": [
                {"line_number": 1, "quantity_ordered": "20.00", "unit_price": "10.0000"}]},
            {"po_number": "PO-B", "currency": "EUR", "lines": [
                {"line_number": 1, "quantity_ordered": "30", "unit_price": "9.50"}]},
        ],
        "goods_received_notes": [
            {"po_number": "PO-A", "lines": [{"po_line_number": 1, "quantity_received": "4.25"}]},
            {"po_number": "PO-A", "lines": [{"po_line_number": 1, "quantity_received": "5.750"}]},
            {"po_number": "PO-B", "lines": [{"po_line_number": 1, "quantity_received": "8"}]},
        ],
        "approved_quantities": [
            {"po_number": "PO-A", "po_line_number": 1, "quantity": "1.500"}],
        "configuration": {"configuration_id": 7, "config": {"price_tolerance": "0.0200"}},
    }
    return invoice, context


def test_po_local_receipts_and_approved_subtraction_have_exact_row_contract():
    invoice, context = snapshots()
    assert comparison_rows(invoice, context) == [
        {"line_index": 0, "description": "First", "quantity_invoiced": "2.00",
         "po_number": "PO-A", "po_line_number": 1, "quantity_ordered": "20.00",
         "quantity_received": "10.000", "quantity_previously_approved": "1.500",
         "quantity_available": "8.500", "po_net_unit_price": "10.0000",
         "invoice_net_unit_price": "10.0100", "invoice_currency": "USD",
         "po_currency": "USD", "tolerance": "0.0200", "association_status": "explicit",
         "findings": []},
        {"line_index": 1, "description": "Second", "quantity_invoiced": "3.0",
         "po_number": "PO-B", "po_line_number": 1, "quantity_ordered": "30",
         "quantity_received": "8", "quantity_previously_approved": "0",
         "quantity_available": "8", "po_net_unit_price": "9.50",
         "invoice_net_unit_price": None, "invoice_currency": "USD",
         "po_currency": "EUR", "tolerance": "0.0200", "association_status": "explicit",
         "findings": []},
    ]


@pytest.mark.parametrize("approved,previous,available", [(None, None, None), ([], "0", "10.000")])
def test_unknown_approved_is_not_authoritative_empty(approved, previous, available):
    invoice, context = snapshots()
    context["approved_quantities"] = approved
    row = comparison_rows(invoice, context)[0]
    assert row["quantity_previously_approved"] == previous
    assert row["quantity_available"] == available
    assert row["quantity_received"] == "10.000"


@pytest.mark.parametrize("notes,received,available", [(None, None, None), ([], "0", "-1.500")])
def test_unknown_receipts_are_not_zero(notes, received, available):
    invoice, context = snapshots()
    context["goods_received_notes"] = notes
    row = comparison_rows(invoice, context)[0]
    assert row["quantity_received"] == received
    assert row["quantity_available"] == available


@pytest.mark.parametrize("associations", [None, [], [{"line_index": 0, "association": None}]])
def test_raw_reference_never_becomes_association_or_net_price(associations):
    invoice, context = snapshots()
    invoice["associations"] = associations
    invoice["net_unit_prices"] = None
    rows = comparison_rows(invoice, context)
    assert len(rows) == 2
    for row in rows:
        assert row["association_status"] == "unresolved"
        for field in ("po_number", "po_line_number", "quantity_ordered", "quantity_received",
                      "quantity_previously_approved", "quantity_available", "po_net_unit_price",
                      "po_currency", "invoice_net_unit_price"):
            assert row[field] is None


@pytest.mark.parametrize("missing", ["po", "line", "reference"])
def test_missing_or_unreferenced_target_retains_line_without_claiming_reliable_link(missing):
    invoice, context = snapshots()
    if missing == "po":
        context["purchase_orders"] = []
    elif missing == "line":
        context["purchase_orders"][0]["lines"] = []
    else:
        invoice["invoice"]["po_references"] = ["PO-B"]
    row = comparison_rows(invoice, context)[0]
    assert row["description"] == "First"
    assert row["po_number"] == "PO-A"
    assert row["po_line_number"] == 1
    assert row["association_status"] == "unresolved"
    assert row["quantity_available"] is None
    assert row["po_net_unit_price"] is None


def test_shared_target_keeps_same_available_not_partitioned():
    invoice, context = snapshots()
    invoice["associations"][1]["po_number"] = "PO-A"
    rows = comparison_rows(invoice, context)
    assert [row["quantity_available"] for row in rows] == ["8.500", "8.500"]
    assert [row["quantity_invoiced"] for row in rows] == ["2.00", "3.0"]


def test_arithmetic_preserves_precision_under_small_ambient_context():
    invoice, context = snapshots()
    context["goods_received_notes"][0]["lines"][0]["quantity_received"] = "123456789012345678901234567890.00001"
    with localcontext() as decimal_context:
        decimal_context.prec = 3
        row = comparison_rows(invoice, context)[0]
    assert row["quantity_received"] == "123456789012345678901234567895.75001"
    assert row["quantity_available"] == "123456789012345678901234567894.25001"


def test_readable_findings_scope_json_safety_and_detachment():
    invoice, context = snapshots()
    findings = [
        RuleResult("QUANTITY", "HOLD", "Invoiced quantity exceeds available quantity.", "PO-A", 0, 1),
        {"code": "PO_CURRENCY", "outcome": "HOLD", "reason": "Currencies differ; no conversion.",
         "po_number": "PO-B", "line_index": None, "po_line_number": None},
        {"code": "SUPPLIER", "outcome": "PENDING", "reason": "Supplier evidence missing."},
    ]
    original = deepcopy((invoice, context, findings))
    rows = comparison_rows(invoice, context, findings)
    assert [f["message"] for f in rows[0]["findings"]] == [
        "Invoiced quantity exceeds available quantity.", "Supplier evidence missing."]
    assert rows[0]["findings"][0] == {
        "rule": "QUANTITY", "message": "Invoiced quantity exceeds available quantity.",
        "evidence": {"outcome": "HOLD", "po_number": "PO-A", "line_index": 0, "po_line_number": 1}}
    assert rows[1]["findings"][0]["message"] == "Currencies differ; no conversion."
    json.dumps(rows, allow_nan=False)
    rows[0]["findings"][1]["evidence"]["outcome"] = "changed"
    assert (invoice, context, findings) == original


def test_code_only_finding_is_rejected_rather_than_displayed_as_explanation():
    invoice, context = snapshots()
    with pytest.raises(ValueError, match="readable reason"):
        comparison_rows(invoice, context, [{"code": "UNKNOWN"}])


def test_empty_invoice_and_absent_configuration_remain_passive_projection():
    invoice, context = snapshots()
    context["configuration"] = None
    assert comparison_rows(invoice, context)[0]["tolerance"] is None
    invoice["invoice"]["lines"] = []
    assert comparison_rows(invoice, context) == []
