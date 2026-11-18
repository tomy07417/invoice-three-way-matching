"""Original files/reference JSON; controlled DTOs, not live extraction evidence.

The net variant supplies trusted net evidence to test conditional Auto-Pay. The
unknown variant preserves that uncertainty and must never claim Auto-Pay.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.dtos.invoice_documents import InvoiceDraft, InvoiceLineDraft
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


@pytest.mark.parametrize('basis', ['unknown', 'net'])
def test_original_documents_associate_without_rewriting_extracted_references(review, migrated_engine, basis):
    client, _, seed = review
    reference = read_reference_data(ROOT/'data/reference-data/reference-data')
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    admitted = []
    for index, supplier_id, number, day, subtotal, tax, total, po, descriptions, quantities, prices, amounts in CASES:
        supplier = next(item for item in reference.suppliers if item.supplier_id == supplier_id)
        lines = tuple(InvoiceLineDraft(text, Decimal(quantity), Decimal(price), Decimal(amount),
                                       None, None, None, basis)
                      for text, quantity, price, amount in zip(descriptions, quantities, prices, amounts))
        draft = InvoiceDraft(supplier.legal_name, supplier.tax_id, number, date.fromisoformat(day), None,
                             (po,), 'USD', Decimal(subtotal), Decimal(tax), Decimal(total), lines)
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
        assert value['evaluation']['result']['final_status'] == ('Auto-Pay' if basis == 'net' else None)
        assert value['evaluation']['input_snapshot']['associations'] == [
            {'line_index': index, 'po_number': po, 'po_line_number': index+1} for index in range(count)]
        assert value['extraction'] == value['original_extraction']
        assert all(line['po_number'] is None and line['po_line_number'] is None
                   and line['unit_price_basis'] == basis for line in value['extraction']['lines'])
        assert all(row['association_method'] == 'description' for row in value['comparison_rows'])
        assert all(result['outcome'] == 'PASS' for result in value['evaluation']['result']['rules']
                   if result['code'] in ('LINE_ASSOCIATION', 'QUANTITY'))
        assert client.get(value['document_url']).content == content
