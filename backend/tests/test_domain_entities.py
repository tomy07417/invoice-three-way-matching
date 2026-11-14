"""Pure domain records preserve source data and invoice extraction values."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from backend.app.domain.entities import (
    GoodsReceivedNote,
    GoodsReceivedNoteLine,
    Invoice,
    InvoiceLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)


REFERENCE_DATA = (
    Path(__file__).resolve().parents[2] / "data/reference-data/reference-data"
)


def source_records(filename):
    return json.loads(
        (REFERENCE_DATA / filename).read_text(),
        parse_float=Decimal,
        parse_int=Decimal,
    )


def test_supplier_preserves_source_fields_and_inactive_status():
    records = source_records("suppliers.json")
    suppliers = tuple(Supplier(**record) for record in records)

    assert [asdict(supplier) for supplier in suppliers] == records
    assert suppliers[0].supplier_id == "SUP-001"
    assert suppliers[0].address == "Kungsgatan 12, 111 35 Stockholm, Sweden"
    assert suppliers[-1].status == "inactive"


def test_purchase_orders_preserve_source_lines_dates_and_decimals():
    for record in source_records("purchase_orders.json"):
        lines = tuple(
            PurchaseOrderLine(**{**line, "line_number": int(line["line_number"])})
            for line in record["lines"]
        )
        po_date = date.fromisoformat(record["po_date"])
        order = PurchaseOrder(**{**record, "po_date": po_date, "lines": lines})

        assert asdict(order) == {**record, "po_date": po_date, "lines": tuple(record["lines"])}
        assert order.po_date is po_date
        assert isinstance(order.lines, tuple)
        for line, raw in zip(order.lines, record["lines"], strict=True):
            assert isinstance(line.line_number, int)
            assert isinstance(line.quantity_ordered, Decimal)
            assert isinstance(line.unit_price, Decimal)
            assert line.quantity_ordered is raw["quantity_ordered"]
            assert line.unit_price is raw["unit_price"]


def test_grns_preserve_local_line_references_across_multiple_receipts():
    notes = []
    for record in source_records("goods_received_notes.json"):
        lines = tuple(
            GoodsReceivedNoteLine(
                po_line_number=int(line["po_line_number"]),
                quantity_received=line["quantity_received"],
            )
            for line in record["lines"]
        )
        received_date = date.fromisoformat(record["received_date"])
        note = GoodsReceivedNote(
            **{**record, "received_date": received_date, "lines": lines}
        )
        assert asdict(note) == {
            **record, "received_date": received_date, "lines": tuple(record["lines"])
        }
        assert note.received_date is received_date
        assert isinstance(note.lines, tuple)
        for line, raw in zip(note.lines, record["lines"], strict=True):
            assert isinstance(line.po_line_number, int)
            assert isinstance(line.quantity_received, Decimal)
            assert line.quantity_received is raw["quantity_received"]
        notes.append(note)

    receipts = [note for note in notes if note.po_number == "PO-2026-0102"]
    assert [note.grn_number for note in receipts] == ["GRN-2026-0318", "GRN-2026-0322"]
    assert [note.lines[0].po_line_number for note in receipts] == [1, 1]
    assert [note.lines[0].quantity_received for note in receipts] == [Decimal("10"), Decimal("5")]


@pytest.fixture
def invoice():
    return Invoice(
        supplier_name="Nordic Office Supply AB",
        supplier_tax_id="SE556123456701",
        invoice_number="INV-001",
        invoice_date=date(2026, 6, 20),
        due_date=date(2026, 7, 20),
        po_references=("PO-2026-0101", "PO-not-found"),
        currency="USD",
        subtotal=Decimal("24.5000"),
        tax_amount=Decimal("4.9000"),
        total_amount=Decimal("29.4000"),
        lines=(InvoiceLine(
            description="Copy paper A4",
            quantity=Decimal("1.0000"),
            unit_price=Decimal("24.5000"),
            line_total=Decimal("24.5000"),
            po_line_reference="PO-2026-0101 / line 1 (extracted text)",
        ),),
    )


def test_invoice_preserves_all_extraction_fields_and_exact_decimal_values(invoice):
    assert asdict(invoice) == {
        "supplier_name": "Nordic Office Supply AB",
        "supplier_tax_id": "SE556123456701",
        "invoice_number": "INV-001",
        "invoice_date": date(2026, 6, 20),
        "due_date": date(2026, 7, 20),
        "po_references": ("PO-2026-0101", "PO-not-found"),
        "currency": "USD",
        "subtotal": Decimal("24.5000"),
        "tax_amount": Decimal("4.9000"),
        "total_amount": Decimal("29.4000"),
        "lines": ({
            "description": "Copy paper A4",
            "quantity": Decimal("1.0000"),
            "unit_price": Decimal("24.5000"),
            "line_total": Decimal("24.5000"),
            "po_line_reference": "PO-2026-0101 / line 1 (extracted text)",
        },),
    }
    assert type(invoice.invoice_date) is date
    assert type(invoice.due_date) is date
    for value in (
        invoice.subtotal, invoice.tax_amount, invoice.total_amount,
        invoice.lines[0].quantity, invoice.lines[0].unit_price, invoice.lines[0].line_total,
    ):
        assert isinstance(value, Decimal)
        assert value.as_tuple().exponent == -4


def test_invoice_allows_missing_optional_fields_and_no_po_reference(invoice):
    line = replace(invoice.lines[0], po_line_reference=None)
    extracted = replace(invoice, due_date=None, po_references=(), lines=(line,))

    assert extracted.due_date is None
    assert extracted.po_references == ()
    assert extracted.lines[0].po_line_reference is None
    assert extracted.invoice_number == invoice.invoice_number


def test_decimal_inputs_are_not_rounded_or_replaced(invoice):
    precise = Decimal("0.1234567890123456789012345678900")
    po_line = PurchaseOrderLine(1, "ITEM", "Item", precise, precise)
    grn_line = GoodsReceivedNoteLine(1, precise)
    invoice_line = replace(invoice.lines[0], quantity=precise, unit_price=precise, line_total=precise)
    extracted = replace(invoice, subtotal=precise, tax_amount=precise, total_amount=precise)

    for value in (
        po_line.quantity_ordered, po_line.unit_price, grn_line.quantity_received,
        invoice_line.quantity, invoice_line.unit_price, invoice_line.line_total,
        extracted.subtotal, extracted.tax_amount, extracted.total_amount,
    ):
        assert value is precise
        assert value.as_tuple() == precise.as_tuple()


def test_records_and_tuple_collections_are_immutable(invoice):
    po_line = PurchaseOrderLine(1, "ITEM", "Item", Decimal("1"), Decimal("2"))
    grn_line = GoodsReceivedNoteLine(1, Decimal("1"))
    records = (
        Supplier("SUP", "Supplier", "TAX", "USD", "inactive", "Address"),
        po_line,
        PurchaseOrder("PO", "SUP", date(2026, 6, 1), "USD", "closed", (po_line,)),
        grn_line,
        GoodsReceivedNote("GRN", "PO", date(2026, 6, 2), (grn_line,)),
        invoice.lines[0],
        invoice,
    )
    for record in records:
        field_name = next(iter(record.__dataclass_fields__))
        with pytest.raises(FrozenInstanceError):
            setattr(record, field_name, getattr(record, field_name))
    with pytest.raises(TypeError):
        invoice.lines[0] = invoice.lines[0]
    with pytest.raises(TypeError):
        invoice.po_references[0] = "changed"
