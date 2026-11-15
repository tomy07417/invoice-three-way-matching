"""Public engine contract for confirmed policies and immutable evaluation snapshots."""
import ast
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from backend.tests.test_matching_rules import invoice, po, grns
from backend.app.domain.entities import Supplier
from backend.app.domain.matching.types import (
    InvoiceHistoryEntry, LineAssociation, MatchingConfig,
)
from backend.app.domain.matching.engine import evaluate


SUPPLIER = Supplier("SUP-Z", "Resolved supplier", "TAX", "EUR", "active", "Address")


def run(invoice, po, grns, **kwargs):
    inputs = dict(supplier=SUPPLIER, associations={0: LineAssociation(po.po_number, 7)},
                  approved_quantities={}, invoice_history=(),
                  net_unit_prices={index: line.unit_price for index, line in enumerate(invoice.lines)})
    inputs.update(kwargs)
    orders = inputs.pop("purchase_orders", (po,))
    return evaluate(invoice, orders, grns, **inputs)


def finding(result, code):
    return next(item for item in result.rules if item.code == code)


def history(invoice, **changes):
    return replace(InvoiceHistoryEntry(SUPPLIER.supplier_id, "OTHER-NUMBER", invoice.invoice_date,
                                        invoice.currency, Decimal("999")), **changes)


def test_history_input_preserves_original_number_and_is_immutable(invoice):
    record = history(invoice, invoice_number=" ab- 123 ")
    assert record.invoice_number == " ab- 123 "
    with pytest.raises(FrozenInstanceError):
        record.invoice_number = "changed"


def test_completed_pay_is_deterministic_and_immutable(invoice, po, grns):
    before = (asdict(invoice), asdict(po), tuple(asdict(note) for note in grns))
    result = run(invoice, po, grns)
    assert result == run(invoice, po, grns)
    assert result.provisional_status == result.final_status == "Auto-Pay"
    assert result.complete and result.pending == ()
    assert all(rule.outcome == "PASS" and rule.reason for rule in result.rules)
    assert result.config == MatchingConfig()
    assert before == (asdict(invoice), asdict(po), tuple(asdict(note) for note in grns))
    for record, field in ((result, "final_status"), (result.config, "price_tolerance"),
                          (result.rules[0], "reason")):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, None)


@pytest.mark.parametrize("kind,expected", [("reject_hold", "Rejected"),
    ("reject_pay", "Rejected"), ("hold_pay", "Held for Review")])
def test_pairwise_precedence(invoice, po, grns, kind, expected):
    if kind.startswith("reject"):
        invoice = replace(invoice, invoice_date=po.po_date.replace(day=1))
    if kind.endswith("hold") or kind == "hold_pay":
        invoice = replace(invoice, lines=(replace(invoice.lines[0], quantity=Decimal("6")),))
    result = run(invoice, po, grns)
    assert result.complete and result.pending == ()
    assert result.provisional_status == result.final_status == expected
    assert finding(result, "UNIT_PRICE").outcome == "PASS"
    if kind == "reject_hold":
        assert finding(result, "QUANTITY").outcome == "HOLD"


@pytest.mark.parametrize("quantity,day,expected", [("5", 2, "Auto-Pay"),
    ("6", 2, "Held for Review"), ("6", 1, "Rejected")])
def test_missing_history_never_hides_available_rules(invoice, po, grns, quantity, day, expected):
    invoice = replace(invoice, invoice_date=po.po_date.replace(day=day),
                      lines=(replace(invoice.lines[0], quantity=Decimal(quantity)),))
    result = run(invoice, po, grns, invoice_history=None)
    assert result.provisional_status == expected
    assert result.final_status is None and not result.complete
    assert {rule.code for rule in result.pending} == {"DUPLICATE", "SIMILAR_INVOICE"}
    assert all(rule.reason == "Invoice history has not been supplied." for rule in result.pending)
    assert finding(result, "QUANTITY").outcome == ("HOLD" if quantity == "6" else "PASS")


def test_missing_context_is_not_zero_or_policy_clearance(invoice, po, grns):
    result = evaluate(invoice, (po,), grns, associations={0: LineAssociation(po.po_number, 7)})
    assert result.final_status is None and not result.complete
    assert {rule.code for rule in result.pending} == {
        "DUPLICATE", "SIMILAR_INVOICE", "QUANTITY", "UNIT_PRICE", "SUPPLIER_ACTIVITY", "SUPPLIER"}
    assert all(rule.reason for rule in result.pending)


def test_normalized_duplicate_rejects_regardless_of_other_fields(invoice, po, grns):
    prior = history(invoice, invoice_number=" novel 739 ", total_amount=Decimal("1234"), currency="USD")
    result = run(invoice, po, grns, invoice_history=(prior,))
    assert result.complete and result.final_status == "Rejected"
    assert finding(result, "DUPLICATE").outcome == "REJECT"


def test_same_total_date_different_number_holds(invoice, po, grns):
    prior = history(invoice, total_amount=invoice.total_amount)
    result = run(invoice, po, grns, invoice_history=(prior,))
    assert result.complete and result.final_status == "Held for Review"
    assert finding(result, "DUPLICATE").outcome == "PASS"
    assert finding(result, "SIMILAR_INVOICE").outcome == "HOLD"


@pytest.mark.parametrize("associations", [{0: None}, {}, {0: LineAssociation("OTHER", 7)},
                                         {0: LineAssociation("ORDER-Z", 999)}])
def test_unreliable_association_holds_whole_invoice(invoice, po, grns, associations):
    result = run(invoice, po, grns, associations=associations)
    rule = finding(result, "LINE_ASSOCIATION")
    assert (rule.outcome, rule.line_index) == ("HOLD", 0)
    assert rule.reason
    assert result.complete and result.final_status == "Held for Review"


def test_resolved_supplier_required_without_name_or_tax_guess(invoice, po, grns):
    result = run(invoice, po, grns, supplier=None)
    assert finding(result, "SUPPLIER").outcome == "PENDING"
    assert result.final_status is None and not result.complete
    mismatch = run(invoice, po, grns, supplier=replace(SUPPLIER, supplier_id="OTHER"))
    assert mismatch.final_status == "Rejected" and mismatch.complete


@pytest.mark.parametrize("status,expected", [("inactive", "Held for Review"), ("active", "Auto-Pay")])
def test_supplier_activity(invoice, po, grns, status, expected):
    result = run(invoice, po, grns, supplier=replace(SUPPLIER, status=status))
    assert result.complete and result.final_status == expected
    assert finding(result, "SUPPLIER_ACTIVITY").outcome == ("HOLD" if status == "inactive" else "PASS")


@pytest.mark.parametrize("closed", [False, True])
def test_inactive_and_closed_never_suppress_rejection(invoice, po, grns, closed):
    changed_po = replace(po, status="closed") if closed else po
    invoice = replace(invoice, invoice_date=po.po_date.replace(day=1))
    result = run(invoice, changed_po, grns, supplier=replace(SUPPLIER, status="inactive"))
    assert result.final_status == "Rejected" and result.complete
    assert finding(result, "SUPPLIER_ACTIVITY").outcome == "HOLD"
    assert finding(result, "QUANTITY").outcome == "PASS"
    assert finding(result, "UNIT_PRICE").outcome == "PASS"


def test_closed_po_holds_but_keeps_duplicate_and_matching_checks(invoice, po, grns):
    result = run(invoice, replace(po, status="closed"), grns)
    assert result.complete and result.final_status == "Held for Review"
    assert finding(result, "PO_STATUS").outcome == "HOLD"
    assert finding(result, "DUPLICATE").outcome == "PASS"
    assert finding(result, "QUANTITY").outcome == "PASS"
    duplicate = history(invoice, invoice_number=invoice.invoice_number)
    assert run(invoice, replace(po, status="closed"), grns, invoice_history=(duplicate,)).final_status == "Rejected"


def test_each_reference_checked_and_missing_references_reject(invoice, po, grns):
    result = run(replace(invoice, po_references=(po.po_number, "ABSENT")), po, grns)
    assert [(r.po_number, r.outcome) for r in result.rules if r.code == "PO_EXISTS"] == [
        (po.po_number, "PASS"), ("ABSENT", "REJECT")]
    assert result.final_status == "Rejected"
    result = run(replace(invoice, po_references=()), po, grns, associations={0: None})
    assert finding(result, "PO_REFERENCES").outcome == "REJECT"
    assert result.final_status == "Rejected"


def test_currency_mismatch_holds_without_price_comparison(invoice, po, grns):
    result = run(replace(invoice, currency="USD"), po, grns, net_unit_prices=None)
    assert finding(result, "UNIT_PRICE").outcome == "HOLD"
    assert result.final_status == "Held for Review" and result.complete
    assert finding(result, "QUANTITY").outcome == "PASS"


def test_net_unit_price_is_explicit_and_does_not_change_extraction(invoice, po, grns):
    invoice = replace(invoice, lines=(replace(invoice.lines[0], unit_price=Decimal("120")),))
    result = run(invoice, po, grns, net_unit_prices={0: Decimal("100")})
    assert result.final_status == "Auto-Pay"
    assert invoice.lines[0].unit_price == Decimal("120")
    missing = run(invoice, po, grns, net_unit_prices=None)
    assert missing.final_status is None and not missing.complete
    assert finding(missing, "UNIT_PRICE").outcome == "PENDING"


@pytest.mark.parametrize("quantity,expected", [("4", "Auto-Pay"), ("4.0001", "Held for Review")])
def test_approved_consumption_controls_whole_invoice(invoice, po, grns, quantity, expected):
    invoice = replace(invoice, lines=(replace(invoice.lines[0], quantity=Decimal(quantity)),))
    result = run(invoice, po, grns, approved_quantities={LineAssociation(po.po_number, 7): Decimal("1")})
    assert result.final_status == expected and result.complete
    assert finding(result, "QUANTITY").outcome == ("HOLD" if expected == "Held for Review" else "PASS")
    assert not hasattr(result, "partial_payment")


def test_history_does_not_itself_consume_approved_quantities(invoice, po, grns):
    result = run(invoice, po, grns, invoice_history=(history(invoice),), approved_quantities={})
    assert result.final_status == "Auto-Pay"
    missing = run(invoice, po, grns, approved_quantities=None)
    assert missing.final_status is None and finding(missing, "QUANTITY").outcome == "PENDING"


@pytest.mark.parametrize("quantity,expected", [("2.5", "Auto-Pay"), ("3", "Held for Review")])
def test_shared_po_line_compares_combined_invoice_quantity(invoice, po, grns, quantity, expected):
    line = replace(invoice.lines[0], quantity=Decimal(quantity))
    invoice = replace(invoice, lines=(line, line))
    result = run(invoice, po, grns, associations={0: LineAssociation(po.po_number, 7),
                                                1: LineAssociation(po.po_number, 7)})
    quantities = [r for r in result.rules if r.code == "QUANTITY"]
    assert [r.line_index for r in quantities] == [0, 1]
    assert result.final_status == expected and result.complete
    assert all(r.outcome == ("PASS" if expected == "Auto-Pay" else "HOLD") for r in quantities)


def test_grouped_quantities_preserve_exact_decimal_arithmetic(invoice, po, grns):
    lines = (replace(invoice.lines[0], quantity=Decimal("4.9999999999999999999999999999")),
             replace(invoice.lines[0], quantity=Decimal("0.0000000000000000000000000002")))
    invoice = replace(invoice, lines=lines)
    with localcontext() as context:
        context.prec = 3
        result = run(invoice, po, grns, associations={i: LineAssociation(po.po_number, 7) for i in (0, 1)})
        assert context.prec == 3
    assert result.final_status == "Held for Review" and result.complete


def test_multiple_pos_validate_every_supplier(invoice, po, grns):
    other = replace(po, po_number="OTHER", supplier_id="FOREIGN")
    invoice = replace(invoice, po_references=(po.po_number, other.po_number))
    result = run(invoice, po, grns, purchase_orders=(po, other))
    assert result.final_status == "Rejected" and result.complete
    assert [(r.po_number, r.outcome) for r in result.rules if r.code == "SUPPLIER"] == [
        (po.po_number, "PASS"), (other.po_number, "REJECT")]
    resolved = run(invoice, po, grns, purchase_orders=(po, replace(other, supplier_id="SUP-Z")))
    assert resolved.complete and resolved.final_status == "Auto-Pay"


@pytest.mark.parametrize("owner,expected", [("SUP-Z", "Held for Review"), ("FOREIGN", "Rejected")])
def test_currency_of_every_referenced_po_is_checked(invoice, po, grns, owner, expected):
    other = replace(po, po_number="OTHER", currency="USD", supplier_id=owner)
    invoice = replace(invoice, po_references=(po.po_number, other.po_number))
    result = run(invoice, po, grns, purchase_orders=(po, other))
    assert result.complete and result.final_status == expected
    mismatch = [rule for rule in result.rules if rule.code == "PO_CURRENCY" and rule.po_number == "OTHER"]
    assert len(mismatch) == 1 and mismatch[0].outcome == "HOLD"
    assert finding(result, "UNIT_PRICE").outcome == "PASS"


def test_later_grn_is_received_snapshot_not_invoice_date_cutoff(invoice, po, grns):
    later = tuple(replace(note, received_date=invoice.invoice_date.replace(day=20)) for note in grns)
    assert run(invoice, po, later).final_status == "Auto-Pay"


def test_invalid_context_is_rejected_not_silently_overwritten(invoice, po, grns):
    with pytest.raises(ValueError, match="Duplicate PO number"):
        run(invoice, po, grns, purchase_orders=(po, po))
    with pytest.raises(ValueError, match="Invoice line index"):
        run(invoice, po, grns, associations={9: None})
    with pytest.raises(ValueError, match="Duplicate GRN number"):
        run(invoice, po, grns + (grns[0],))


@pytest.mark.parametrize("reference", ["0", "-100"])
def test_undefined_price_basis_is_incomplete(invoice, po, grns, reference):
    po = replace(po, lines=(replace(po.lines[0], unit_price=Decimal(reference)),))
    result = run(invoice, po, grns)
    rule = finding(result, "UNIT_PRICE")
    assert (rule.outcome, rule.po_number, rule.line_index, rule.po_line_number) == (
        "PENDING", po.po_number, 0, 7)
    assert rule.reason == "Nonpositive PO price needs a percentage-basis policy."
    assert result.pending == (rule,) and not result.complete
    assert result.final_status is None


def test_result_does_not_retain_mutable_input_mappings(invoice, po, grns):
    links, approved, prices = {0: LineAssociation(po.po_number, 7)}, {}, {0: Decimal("100")}
    result = run(invoice, po, grns, associations=links, approved_quantities=approved, net_unit_prices=prices)
    links[0] = None
    approved[LineAssociation(po.po_number, 7)] = Decimal("999")
    prices[0] = Decimal("999")
    assert result.complete and result.final_status == "Auto-Pay"
    assert run(invoice, po, grns, associations=links).final_status == "Held for Review"


def test_parameter_snapshot_changes_only_new_evaluation(invoice, po, grns):
    invoice = replace(invoice, lines=(replace(invoice.lines[0], unit_price=Decimal("103")),))
    old = run(invoice, po, grns)
    changed = run(invoice, po, grns, config=MatchingConfig(Decimal("0.03"), Decimal("1")))
    assert old.final_status == "Held for Review" and changed.final_status == "Auto-Pay"
    assert old.config.price_tolerance == Decimal("0.02")
    assert changed.config.analyst_approval_limit == Decimal("1")


def test_matching_imports_are_standard_library_or_domain_only():
    directory = Path(__file__).resolve().parents[1] / "app/domain/matching"
    expected_modules = {"engine.py", "rules.py", "types.py", "duplicates.py"}
    assert directory.is_dir()
    paths = tuple(directory.glob("*.py"))
    assert expected_modules <= {path.name for path in paths}
    allowed = {"dataclasses", "datetime", "decimal", "typing", "collections"}
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.level <= 2
                    assert node.module is None or node.module.split(".")[0] in {
                        "entities", "types", "duplicates", "pending_policies"}
                else:
                    assert node.module.split(".")[0] in allowed
