"""Original files/reference JSON; controlled DTOs, not live extraction evidence.

The net variant supplies trusted net evidence to test conditional Auto-Pay. The
unknown variant preserves that uncertainty and must never claim Auto-Pay.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.dtos.invoice_documents import InvoiceDraft, InvoiceLineDraft
from app.domain.matching.price_basis import AccountingEvidence
from app.application.use_cases.import_reference_data import import_reference_data
from app.infrastructure.reference_data.json_reader import read_reference_data
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from test_invoice_list import login
from test_review_api import review

ROOT = Path(__file__).resolve().parents[2]
CASES = (
    (1, 'SUP-001', 'NOS-2026-1187', '2026-06-25', '1514.00', '317.94', '1831.94', 'PO-2026-0101',
     ('Copy paper A4, 80g, box of 5 reams', 'Toner cartridge black, HP 59X compatible'), ('40', '6'), ('24.50', '89.00'), ('980.00', '534.00')),
    (2, 'SUP-003', 'MH-2026-0457', '2026-07-02', '3135.00', '658.35', '3793.35', 'PO-2026-0102',
     ('USB-C laptop docking station, dual 4K', 'USB-C to USB-C cable, 2 m'), ('15', '30'), ('185.00', '12.00'), ('2775.00', '360.00')),
    (3, 'SUP-005', 'SWC-4471', '2026-07-12', '810.00', '170.10', '980.10', 'PO-2026-0103',
     ('Team lunch catering, per person',), ('45',), ('18.00',), ('810.00',)),
)


@pytest.mark.parametrize('case', ['price', 'quantity', 'ambiguous'])
def test_inv05_po_only_identity_preserves_price_receipt_and_ambiguity_guards(review, migrated_engine, case):
    client, _, seed = review
    reference = read_reference_data(ROOT / 'data/reference-data/reference-data')
    po_number = 'PO-2026-0105'
    if case == 'ambiguous':
        reference = replace(reference, purchase_orders=tuple(
            replace(po, lines=po.lines + (replace(po.lines[0], line_number=2, unit_price=Decimal('999')),))
            if po.po_number == po_number else po for po in reference.purchase_orders))
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    supplier = next(item for item in reference.suppliers if item.supplier_id == 'SUP-001')
    # Actual offline PDF facts for price; quantity variant is deliberately synthetic.
    quantity, price, subtotal, tax, total = (
        ('13', '210.00', '2730.00', '573.30', '3303.30') if case == 'quantity'
        else ('12', '218.40', '2620.80', '550.37', '3171.17'))
    line = InvoiceLineDraft('Ergonomic office chair, mesh back', Decimal(quantity), Decimal(price),
                            Decimal(subtotal), None, po_number, None, 'unknown')
    draft = InvoiceDraft(supplier.legal_name, supplier.tax_id, 'NOS-2026-1243', date(2026, 7, 30),
                         date(2026, 8, 29), (po_number,), 'USD', Decimal(subtotal), Decimal(tax),
                         Decimal(total), (line,), AccountingEvidence(True, ()))
    path = ROOT / 'data/invoices/invoices/INV-05.pdf'
    invoice_id = seed(f'INV05-{case}', draft=draft, document=(path.name, path.read_bytes(), 'application/pdf'))
    login(client, 'analyst')
    response = client.get(f'/review/{invoice_id}')
    assert response.status_code == 200
    value = response.json()
    assert value['evaluation']['result']['final_status'] == 'Held for Review'
    assert value['payment_blocked'] is True
    assert value['extraction'] == value['original_extraction']
    assert value['extraction']['lines'][0]['po_number'] == po_number
    assert value['extraction']['lines'][0]['po_line_number'] is None
    rules = value['evaluation']['result']['rules']
    if case == 'ambiguous':
        assert any(rule['code'] == 'LINE_ASSOCIATION' and rule['outcome'] == 'HOLD' for rule in rules)
        assert value['evaluation']['input_snapshot']['associations'] == []
    else:
        assert all(rule['outcome'] == 'PASS' for rule in rules if rule['code'] == 'LINE_ASSOCIATION')
        assert value['evaluation']['input_snapshot']['associations'] == [
            {'line_index': 0, 'po_number': po_number, 'po_line_number': 1}]
        assert value['comparison_rows'][0]['association_method'] == 'description'
        expected = 'QUANTITY' if case == 'quantity' else 'UNIT_PRICE'
        assert any(rule['code'] == expected and rule['outcome'] == 'HOLD' for rule in rules)


@pytest.mark.parametrize('basis', ['unknown', 'net', 'arithmetic'])
def test_original_documents_associate_without_rewriting_extracted_references(review, migrated_engine, basis):
    client, _, seed = review
    reference = read_reference_data(ROOT/'data/reference-data/reference-data')
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    admitted = []
    for index, supplier_id, number, day, subtotal, tax, total, po, descriptions, quantities, prices, amounts in CASES:
        supplier = next(item for item in reference.suppliers if item.supplier_id == supplier_id)
        declared_basis = 'unknown' if basis == 'arithmetic' else basis
        lines = tuple(InvoiceLineDraft(text, Decimal(quantity), Decimal(price), Decimal(amount),
                                       None, None, None, declared_basis)
                      for text, quantity, price, amount in zip(descriptions, quantities, prices, amounts))
        draft = InvoiceDraft(supplier.legal_name, supplier.tax_id, number, date.fromisoformat(day), None,
                             (po,), 'USD', Decimal(subtotal), Decimal(tax), Decimal(total), lines,
                             AccountingEvidence(True, ()) if basis == 'arithmetic' else None)
        path = ROOT/f'data/invoices/invoices/INV-{index:02d}.{"png" if index == 3 else "pdf"}'
        content = path.read_bytes()
        case = seed(number, draft=draft, document=(path.name, content,
                    'image/png' if index == 3 else 'application/pdf'))
        admitted.append((case, po, len(lines), content))
    login(client, 'analyst')
    for case, po, count, content in admitted:
        response = client.get(f'/review/{case}')
        assert response.status_code == 200
        value = response.json()
        assert value['evaluation']['result']['final_status'] == ('Auto-Pay' if basis != 'unknown' else None)
        assert value['evaluation']['input_snapshot']['associations'] == [
            {'line_index': index, 'po_number': po, 'po_line_number': index+1} for index in range(count)]
        assert value['extraction'] == value['original_extraction']
        assert all(line['po_number'] is None and line['po_line_number'] is None
                   and line['unit_price_basis'] == ('unknown' if basis == 'arithmetic' else basis)
                   for line in value['extraction']['lines'])
        assert all(row['association_method'] == 'description' for row in value['comparison_rows'])
        assert all(result['outcome'] == 'PASS' for result in value['evaluation']['result']['rules']
                   if result['code'] in ('LINE_ASSOCIATION', 'QUANTITY'))
        if basis == 'arithmetic':
            assert all(row['effective_unit_price_basis'] == 'net' and row['unit_price_basis_source'] == 'arithmetic'
                       for row in value['comparison_rows'])
            assert all(rule['outcome'] == 'PASS' for rule in value['evaluation']['result']['rules'])
        assert client.get(value['document_url']).content == content
