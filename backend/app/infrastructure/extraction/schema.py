"""Strict extraction wire contract and exact conversion to application drafts."""
import json
import re
from datetime import date
from decimal import Decimal

from app.application.dtos.invoice_documents import InvoiceDraft, InvoiceLineDraft
from app.application.dtos.accounting import parse_accounting_evidence


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


TEXT = nullable({"type": "string"})
NUMBER = nullable({"type": "string", "pattern": r"^-?[0-9]+(\.[0-9]+)?$"})
DATE = nullable({"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"})
LINE_SCHEMA = object_schema({
    "description": TEXT, "quantity": NUMBER, "unit_price": NUMBER, "line_total": NUMBER,
    "po_line_reference": TEXT, "po_number": TEXT,
    "po_line_number": nullable({"type": "integer"}),
    "unit_price_basis": {"type": "string", "enum": ["net", "gross", "unknown"]},
})
ADJUSTMENT_SCHEMA = object_schema({
    'kind': {'type': 'string', 'enum': ['discount', 'charge', 'rounding', 'unknown']},
    'stage': {'type': 'string', 'enum': ['before_subtotal', 'after_subtotal', 'unknown']},
    'amount': NUMBER, 'line_index': nullable({'type': 'integer'}),
})
ACCOUNTING_SCHEMA = nullable(object_schema({
    'taxes_separately_stated': nullable({'type': 'boolean'}),
    'adjustments': nullable({'type': 'array', 'items': ADJUSTMENT_SCHEMA}),
}))
INVOICE_SCHEMA = object_schema({
    "supplier_name": TEXT, "supplier_tax_id": TEXT, "invoice_number": TEXT,
    "invoice_date": DATE, "due_date": DATE,
    "po_references": nullable({"type": "array", "items": {"type": "string"}}),
    "currency": TEXT, "subtotal": NUMBER, "tax_amount": NUMBER, "total_amount": NUMBER,
    "lines": {"type": "array", "items": LINE_SCHEMA},
    'accounting_evidence': ACCOUNTING_SCHEMA,
})
EXTRACTION_SCHEMA = object_schema({
    "document_type": nullable({"type": "string", "enum": ["invoice", "quotation", "purchase_order",
        "delivery_note", "credit_note", "receipt", "statement", "other", "unknown"]}),
    "is_invoice": {"type": "boolean"}, "invoice": nullable(INVOICE_SCHEMA),
    "warnings": {"type": "array", "items": {"type": "string"}},
})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON constant")


def load_json(text):
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def _validate(value, schema):
    if "anyOf" in schema:
        if value is None:
            return
        _validate(value, schema["anyOf"][0])
        return
    kind = schema["type"]
    if kind == "object":
        if type(value) is not dict or set(value) != set(schema["properties"]):
            raise ValueError("Invalid object")
        for key, child in schema["properties"].items():
            _validate(value[key], child)
    elif kind == "array":
        if type(value) is not list:
            raise ValueError("Invalid array")
        for item in value:
            _validate(item, schema["items"])
    elif kind == "string":
        if type(value) is not str:
            raise ValueError("Invalid string")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError("Invalid format")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("Invalid enum")
    elif kind == "boolean" and type(value) is not bool:
        raise ValueError("Invalid boolean")
    elif kind == "integer" and type(value) is not int:
        raise ValueError("Invalid integer")


def parse_extraction(text):
    data = load_json(text)
    # Accept the original contract on historical/mock responses. Absence is
    # unknown, never an empty adjustment list or permission to infer.
    if isinstance(data, dict):
        data.setdefault('document_type', None)
        if isinstance(data.get('invoice'), dict):
            data['invoice'].setdefault('accounting_evidence', None)
    _validate(data, EXTRACTION_SCHEMA)
    if data["is_invoice"] != (data["invoice"] is not None):
        raise ValueError("Inconsistent invoice classification")
    if data['document_type'] not in (None, 'unknown') and data['is_invoice'] != (data['document_type'] == 'invoice'):
        raise ValueError("Inconsistent document type")
    if data["invoice"] is None:
        return False, None, tuple(data["warnings"])
    fields = dict(data["invoice"])
    for key in ("invoice_date", "due_date"):
        if fields[key] is not None:
            fields[key] = date.fromisoformat(fields[key])
    for key in ("subtotal", "tax_amount", "total_amount"):
        if fields[key] is not None:
            fields[key] = Decimal(fields[key])
    if fields["po_references"] is not None:
        fields["po_references"] = tuple(fields["po_references"])
    lines = []
    for raw in fields["lines"]:
        line = dict(raw)
        for key in ("quantity", "unit_price", "line_total"):
            if line[key] is not None:
                line[key] = Decimal(line[key])
        lines.append(InvoiceLineDraft(**line))
    fields["lines"] = tuple(lines)
    fields['accounting_evidence'] = parse_accounting_evidence(fields['accounting_evidence'])
    return True, InvoiceDraft(**fields), tuple(data["warnings"])
