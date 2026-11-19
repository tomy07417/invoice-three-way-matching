"""Pure identity resolution: no prices, quantities, I/O or extraction mutations."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.domain.entities import PurchaseOrder, PurchaseOrderLine
from app.domain.matching.types import LineAssociation


def order(number='PO-1', descriptions=('Paper A4 80g', 'Toner HP 59X')):
    return PurchaseOrder(number, 'S1', date(2026, 1, 1), 'USD', 'open', tuple(
        PurchaseOrderLine(index, f'SKU-{index}', description, Decimal('40'), Decimal('24.50'))
        for index, description in zip((3, 9), descriptions)))


def resolve(descriptions, *, orders=None, references=('PO-1',), evidence=None):
    from app.domain.matching.association import InvoiceLineIdentity, resolve_line_associations
    identities = tuple(InvoiceLineIdentity(description, **((evidence or {}).get(index, {})))
                       for index, description in enumerate(descriptions))
    return resolve_line_associations(identities, references, tuple(orders or (order(),)))


@pytest.mark.parametrize('description', ['Paper A4 80g', '  paper  a4\t80G\n'])
def test_exact_description_uses_business_line_number_not_array_position(description):
    result = resolve((description, 'Toner HP 59X'))
    assert {index: value.target for index, value in result.items()} == {
        0: LineAssociation('PO-1', 3), 1: LineAssociation('PO-1', 9)}
    assert all(value.method == 'description' for value in result.values())


@pytest.mark.parametrize('description', ['Paper A3 80g', 'Paper A4 90g', 'Paper-A4 80g', '', None])
def test_no_fuzzy_matching_or_specification_loss(description):
    assert resolve((description,)) == {}


def test_duplicate_descriptions_are_ambiguous_even_with_different_prices():
    duplicate = order(descriptions=('Paper A4 80g', 'Paper A4 80g'))
    duplicate = replace(duplicate, lines=(duplicate.lines[0], replace(duplicate.lines[1], unit_price=Decimal('999'))))
    assert resolve(('Paper A4 80g',), orders=(duplicate,)) == {}


def test_multi_po_ambiguity_does_not_choose_first_or_unreferenced_order():
    assert resolve(('Paper A4 80g',), orders=(order(), order('PO-2')),
                   references=('PO-1', 'PO-2')) == {}
    result = resolve(('Paper A4 80g',), orders=(order('PO-2'), order()))
    assert result[0].target == LineAssociation('PO-1', 3)
    assert resolve(('Paper A4 80g',), orders=(order('PO-2'),)) == {}


@pytest.mark.parametrize('evidence', [
    {'po_number': 'PO-1', 'po_line_number': 3},
    {'po_line_reference': '3'},
    {'po_line_reference': 'PO-1/3'},
    {'po_number': 'PO-1', 'po_line_reference': '3'},
])
def test_valid_explicit_identity_precedes_description(evidence):
    result = resolve(('Paper A4 80g',), evidence={0: evidence})
    assert result[0].target == LineAssociation('PO-1', 3)
    assert result[0].method == 'explicit'


@pytest.mark.parametrize('evidence', [
    {'po_number': 'PO-2', 'po_line_number': 3},
    {'po_number': 'PO-1', 'po_line_number': 1},
    {'po_number': 'PO-1', 'po_line_number': 0},
    {'po_number': 'PO-1', 'po_line_number': True},
    {'po_number': 'PO-2'},
    {'po_line_number': 3},
    {'po_line_number': 3, 'po_line_reference': '3'},
    {'po_number': 'PO-1', 'po_line_reference': '9'},
    {'po_number': 'PO-1', 'po_line_reference': 'unknown line'},
    {'po_line_reference': 'unknown line'},
    {'po_line_reference': '99'},
    pytest.param({'po_line_reference': '9' * 5000}, id='oversized-raw-reference'),
    {'po_number': 'PO-1', 'po_line_number': 3, 'po_line_reference': '9'},
    {'po_number': 'PO-1', 'po_line_number': 9},
])
def test_invalid_partial_or_conflicting_reference_is_not_rescued_by_description(evidence):
    assert resolve(('Paper A4 80g',), evidence={0: evidence}) == {}


def test_raw_line_number_is_ambiguous_across_referenced_orders():
    assert resolve(('Paper A4 80g',), orders=(order(), order('PO-2')),
                   references=('PO-1', 'PO-2'), evidence={0: {'po_line_reference': '3'}}) == {}


def test_explicit_target_disambiguates_duplicate_descriptions():
    duplicate = order(descriptions=('Paper A4 80g', 'Paper A4 80g'))
    result = resolve(('Paper A4 80g',), orders=(duplicate,),
                     evidence={0: {'po_number': 'PO-1', 'po_line_number': 9}})
    assert result[0].target == LineAssociation('PO-1', 9)


def test_po_only_evidence_can_scope_a_unique_normalized_description():
    result = resolve(('  PAPER\ta4 80g ',), evidence={0: {'po_number': 'PO-1'}})
    assert result[0].target == LineAssociation('PO-1', 3)
    assert result[0].method == 'description'


def test_po_only_description_ambiguity_and_unreferenced_orders_remain_unresolved():
    duplicate = order(descriptions=('Paper A4 80g', 'Paper A4 80g'))
    assert resolve(('Paper A4 80g',), orders=(duplicate,), evidence={0: {'po_number': 'PO-1'}}) == {}
    assert resolve(('Paper A4 80g',), evidence={0: {'po_number': 'PO-2'}}) == {}


def test_shared_target_is_retained_for_existing_engine_quantity_aggregation():
    result = resolve(('Paper A4 80g', 'Paper A4 80g'))
    assert len(result) == 2
    assert result[0].target == result[1].target == LineAssociation('PO-1', 3)
