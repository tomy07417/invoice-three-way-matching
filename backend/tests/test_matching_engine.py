"""Public engine contract: findings survive incomplete evaluations."""
import ast
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest

from backend.tests.test_matching_rules import invoice, po, grns
from backend.app.domain.matching.types import (
    LineAssociation, MatchingConfig, PolicyFacts,
)
from backend.app.domain.matching.engine import evaluate


CLEARED = PolicyFacts(duplicate=False, quantities_comparable=True,
                      unit_prices_comparable=True, supplier_activity_cleared=True,
                      po_status_cleared=True)


def run(invoice, po, grns, **kwargs):
    inputs = dict(resolved_supplier_id="SUP-Z", associations={0: LineAssociation("ORDER-Z", 7)},
                  policies=CLEARED)
    inputs.update(kwargs)
    return evaluate(invoice, (po,), grns, **inputs)


def finding(result, code):
    return next(item for item in result.rules if item.code == code)


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
                          (result.rules[0], "reason"), (CLEARED, "duplicate")):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, None)


@pytest.mark.parametrize("kind,expected", [("reject_hold", "Rejected"),
                                           ("reject_pay", "Rejected"),
                                           ("hold_pay", "Held for Review")])
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
def test_pending_duplicate_never_hides_available_rules(invoice, po, grns, quantity, day, expected):
    invoice = replace(invoice, invoice_date=po.po_date.replace(day=day),
                      lines=(replace(invoice.lines[0], quantity=Decimal(quantity)),))
    result = run(invoice, po, grns, policies=replace(CLEARED, duplicate=None))
    assert result.provisional_status == expected
    assert result.final_status is None and not result.complete
    assert result.pending == (finding(result, "DUPLICATE"),)
    assert result.pending[0].reason == "Duplicate definition is pending; no duplicate algorithm is implemented."
    assert finding(result, "QUANTITY").outcome == ("HOLD" if quantity == "6" else "PASS")


def test_default_policies_are_pending_not_pass(invoice, po, grns):
    result = run(invoice, po, grns, policies=PolicyFacts())
    assert result.provisional_status == "Auto-Pay" and result.final_status is None
    assert not result.complete
    assert {rule.code for rule in result.pending} == {
        "DUPLICATE", "QUANTITY", "UNIT_PRICE", "SUPPLIER_ACTIVITY", "PO_STATUS"}
    assert all(rule.outcome == "PENDING" and rule.reason for rule in result.pending)


def test_explicit_verified_duplicate_fact_rejects(invoice, po, grns):
    result = run(invoice, po, grns, policies=replace(CLEARED, duplicate=True))
    assert result.complete and result.final_status == "Rejected"
    assert finding(result, "DUPLICATE").outcome == "REJECT"


@pytest.mark.parametrize("associations,outcome,complete", [
    ({0: None}, "HOLD", True), ({}, "PENDING", False),
])
def test_confirmed_no_match_differs_from_unresolved(invoice, po, grns, associations, outcome, complete):
    result = run(invoice, po, grns, associations=associations)
    rule = finding(result, "LINE_ASSOCIATION")
    assert (rule.outcome, rule.line_index, rule.po_number) == (outcome, 0, None)
    assert rule.reason == ("Invoice line has no corresponding PO line." if complete
                           else "Invoice line association has not been resolved.")
    assert result.complete == complete
    assert result.provisional_status == ("Held for Review" if complete else "Auto-Pay")
    assert result.final_status == ("Held for Review" if complete else None)


def test_resolved_supplier_required_without_name_or_tax_guess(invoice, po, grns):
    result = run(invoice, po, grns, resolved_supplier_id=None)
    assert finding(result, "SUPPLIER").outcome == "PENDING"
    assert result.final_status is None and not result.complete
    mismatch = run(invoice, po, grns, resolved_supplier_id="OTHER")
    assert mismatch.final_status == "Rejected" and mismatch.complete


def test_each_reference_checked_and_missing_references_reject(invoice, po, grns):
    result = run(replace(invoice, po_references=(po.po_number, "ABSENT")), po, grns)
    assert [(r.po_number, r.outcome) for r in result.rules if r.code == "PO_EXISTS"] == [
        (po.po_number, "PASS"), ("ABSENT", "REJECT")]
    assert result.final_status == "Rejected"
    result = run(replace(invoice, po_references=()), po, grns, associations={0: None})
    assert finding(result, "PO_REFERENCES").outcome == "REJECT"
    assert result.final_status == "Rejected"


def test_currency_and_tax_basis_never_guessed(invoice, po, grns):
    for changed_invoice, policies, reason in (
        (replace(invoice, currency="USD"), CLEARED, "Currencies differ; no conversion policy is implemented."),
        (invoice, replace(CLEARED, unit_prices_comparable=False), "Comparable unit-price tax basis has not been established."),
    ):
        result = run(changed_invoice, po, grns, policies=policies)
        assert finding(result, "UNIT_PRICE").reason == reason
        assert result.provisional_status == "Auto-Pay"
        assert result.final_status is None and not result.complete
        assert finding(result, "QUANTITY").outcome == "PASS"


def test_same_po_line_cannot_reuse_receipts(invoice, po, grns):
    invoice = replace(invoice, lines=(invoice.lines[0], invoice.lines[0]))
    result = run(invoice, po, grns, associations={0: LineAssociation("ORDER-Z", 7),
                                                1: LineAssociation("ORDER-Z", 7)})
    quantities = [r for r in result.rules if r.code == "QUANTITY"]
    assert [r.line_index for r in quantities] == [0, 1]
    assert all(r.outcome == "PENDING" and r.po_line_number == 7 for r in quantities)
    assert all(r.reason == "Multiple invoice lines share a PO line; receipt allocation policy is pending."
               for r in quantities)
    assert result.final_status is None and not result.complete


def test_multiple_pos_require_explicit_association(invoice, po, grns):
    other = replace(po, po_number="OTHER")
    invoice = replace(invoice, po_references=(po.po_number, other.po_number))
    result = evaluate(invoice, (po, other), grns, resolved_supplier_id="SUP-Z", policies=CLEARED)
    assert not result.complete and finding(result, "LINE_ASSOCIATION").outcome == "PENDING"
    resolved = evaluate(invoice, (po, other), grns, resolved_supplier_id="SUP-Z", policies=CLEARED,
                        associations={0: LineAssociation("ORDER-Z", 7)})
    assert resolved.complete and resolved.final_status == "Auto-Pay"


@pytest.mark.parametrize("association", [LineAssociation("OTHER", 7), LineAssociation("ORDER-Z", 999)])
def test_impossible_associations_are_not_approval(invoice, po, grns, association):
    result = run(invoice, po, grns, associations={0: association})
    assert finding(result, "LINE_ASSOCIATION").outcome == "PENDING"
    assert result.final_status is None and not result.complete


def test_invalid_context_is_rejected_not_silently_overwritten(invoice, po, grns):
    with pytest.raises(ValueError, match="Duplicate PO number"):
        evaluate(invoice, (po, po), grns)
    with pytest.raises(ValueError, match="Invoice line index"):
        run(invoice, po, grns, associations={9: None})
    with pytest.raises(ValueError, match="Duplicate GRN number"):
        run(invoice, po, grns + (grns[0],))


@pytest.mark.parametrize("reference", ["0", "-100"])
def test_undefined_price_basis_is_incomplete_even_with_policy_facts(invoice, po, grns, reference):
    po = replace(po, lines=(replace(po.lines[0], unit_price=Decimal(reference)),))
    result = run(invoice, po, grns)
    rule = finding(result, "UNIT_PRICE")
    assert (rule.outcome, rule.po_number, rule.line_index, rule.po_line_number) == (
        "PENDING", po.po_number, 0, 7)
    assert rule.reason == "Nonpositive PO price needs a percentage-basis policy."
    assert result.pending == (rule,) and not result.complete
    assert result.final_status is None and result.provisional_status == "Auto-Pay"


def test_result_does_not_retain_mutable_association_mapping(invoice, po, grns):
    links = {0: LineAssociation(po.po_number, 7)}
    result = run(invoice, po, grns, associations=links)
    links[0] = None
    assert result.complete and result.final_status == "Auto-Pay"
    assert run(invoice, po, grns, associations=links).final_status == "Held for Review"


def test_parameter_snapshot_changes_only_new_evaluation(invoice, po, grns):
    invoice = replace(invoice, lines=(replace(invoice.lines[0], unit_price=Decimal("103")),))
    old = run(invoice, po, grns)
    changed = run(invoice, po, grns, config=MatchingConfig(Decimal("0.03"), Decimal("1")))
    assert old.final_status == "Held for Review"
    assert changed.final_status == "Auto-Pay"
    assert old.config.price_tolerance == Decimal("0.02")
    assert changed.config.analyst_approval_limit == Decimal("1")  # carried, not enforced


def test_matching_imports_are_standard_library_or_domain_only():
    directory = Path(__file__).resolve().parents[1] / "app/domain/matching"
    expected_modules = {"engine.py", "rules.py", "types.py", "pending_policies.py"}
    assert directory.is_dir()
    paths = tuple(directory.glob("*.py"))
    assert expected_modules <= {path.name for path in paths}
    allowed = {"dataclasses", "datetime", "decimal", "typing", "collections"}
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.level <= 2
                    assert node.module is None or node.module.split(".")[0] in {
                        "entities", "types", "pending_policies"}
                else:
                    assert node.module.split(".")[0] in allowed
