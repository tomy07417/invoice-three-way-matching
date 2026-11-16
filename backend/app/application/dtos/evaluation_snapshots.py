"""Detached JSON evidence for one evaluation, not mutable domain/adapter state.

Decimal text preserves precision and scale. Lists represent PO-local mapping
keys and explicit zero-based invoice line indices; None remains missing evidence
and an empty list remains an authoritative empty snapshot. These fresh values
are stored with the immutable record and must not be edited by its consumers.
"""
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping

from app.application.dtos.invoice_evaluation import (
    EvaluateInvoiceCommand, RuleConfigurationSnapshot,
)
from app.domain.entities import GoodsReceivedNote, PurchaseOrder, Supplier
from app.domain.matching.types import InvoiceHistoryEntry, LineAssociation


def _json_value(value):
    """Copy domain dataclasses into JSON values without ORM or mapping keys."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def snapshot_input(command: EvaluateInvoiceCommand) -> dict:
    """Retain original extraction and caller-supplied evidence, not guesses."""
    invoice = _json_value(command.invoice)
    invoice["lines"] = [
        {"line_index": index, **line} for index, line in enumerate(invoice["lines"])
    ]
    associations = None
    if command.associations is not None:
        associations = [
            {"line_index": index, **(_json_value(link) if link is not None else {"association": None})}
            for index, link in sorted(command.associations.items())
        ]
    prices = None
    if command.net_unit_prices is not None:
        prices = [
            {"line_index": index, "net_unit_price": _json_value(price)}
            for index, price in sorted(command.net_unit_prices.items())
        ]
    return {
        "invoice": invoice,
        "requested_supplier_id": command.supplier_id,
        "associations": associations,
        "net_unit_prices": prices,
    }


def snapshot_context(
    *,
    supplier: Supplier | None,
    purchase_orders: tuple[PurchaseOrder, ...],
    grns: tuple[GoodsReceivedNote, ...],
    approved_quantities: Mapping[LineAssociation, Decimal] | None,
    invoice_history: tuple[InvoiceHistoryEntry, ...] | None,
    configuration: RuleConfigurationSnapshot,
) -> dict:
    """Copy the actual engine evidence and persisted configuration version."""
    approved = None
    if approved_quantities is not None:
        approved = [
            {**_json_value(link), "quantity": _json_value(quantity)}
            for link, quantity in sorted(
                approved_quantities.items(),
                key=lambda item: (item[0].po_number, item[0].po_line_number),
            )
        ]
    return {
        "supplier": _json_value(supplier),
        "purchase_orders": _json_value(purchase_orders),
        "goods_received_notes": _json_value(grns),
        "approved_quantities": approved,
        "invoice_history": _json_value(invoice_history),
        "configuration": _json_value(configuration),
    }
