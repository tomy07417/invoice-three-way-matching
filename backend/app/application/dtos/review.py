"""Exact, bounded review inputs and detached public values; no HTTP or SQL."""
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from app.application.dtos.invoice_documents import InvoiceDraft, InvoiceLineDraft


class ReviewError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def review_value(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: review_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [review_value(item) for item in value]
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def parse_review_draft(value: dict) -> InvoiceDraft:
    """A correction is a complete nullable draft, never a partial guessed merge."""
    def invalid():
        raise ReviewError('invalid_correction', 'Provide a valid complete extraction with exact decimal strings', 422)

    def shape(data, cls):
        if not isinstance(data, dict) or set(data) != {item.name for item in fields(cls)}:
            invalid()
        return dict(data)

    def text(value, maximum=500):
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > maximum):
            invalid()
        return value

    def decimal(value):
        if value is None:
            return None
        if not isinstance(value, str) or not 1 <= len(value) <= 100:
            invalid()
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            invalid()
        if not parsed.is_finite() or abs(parsed.adjusted()) > 30:
            invalid()
        return parsed

    data = shape(value, InvoiceDraft)
    for key in ('supplier_name', 'supplier_tax_id', 'invoice_number', 'currency'):
        data[key] = text(data[key])
    currency = data['currency']
    if currency is not None and (len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper()):
        invalid()
    for key in ('invoice_date', 'due_date'):
        raw = data[key]
        if raw is not None:
            try:
                if not isinstance(raw, str) or len(raw) != 10:
                    invalid()
                data[key] = date.fromisoformat(raw)
            except ValueError:
                invalid()
    for key in ('subtotal', 'tax_amount', 'total_amount'):
        data[key] = decimal(data[key])
    references = data['po_references']
    if references is not None:
        if not isinstance(references, list) or len(references) > 100:
            invalid()
        data['po_references'] = tuple(text(item) for item in references)
        if any(item is None for item in data['po_references']):
            invalid()
    if not isinstance(data['lines'], list) or len(data['lines']) > 500:
        invalid()
    lines = []
    for raw in data['lines']:
        line = shape(raw, InvoiceLineDraft)
        for key in ('description', 'po_line_reference', 'po_number'):
            line[key] = text(line[key], 2000)
        for key in ('quantity', 'unit_price', 'line_total'):
            line[key] = decimal(line[key])
        number = line['po_line_number']
        if number is not None and (type(number) is not int or not 0 < number <= 2_147_483_647):
            invalid()
        if line['unit_price_basis'] not in ('net', 'gross', 'unknown'):
            invalid()
        lines.append(InvoiceLineDraft(**line))
    data['lines'] = tuple(lines)
    return InvoiceDraft(**data)
