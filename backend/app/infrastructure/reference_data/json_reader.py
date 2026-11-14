"""Read challenge JSON into domain records without lossy float conversion."""
from dataclasses import fields
from datetime import date
from decimal import Decimal, DecimalException
import json
from pathlib import Path

from app.application.dtos.reference_data import ReferenceData, ReferenceDataError
from app.domain.entities import (
    GoodsReceivedNote, GoodsReceivedNoteLine, PurchaseOrder, PurchaseOrderLine, Supplier,
)


def reject_constant(_value):
    raise ReferenceDataError("nonfinite JSON number")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceDataError("duplicate JSON field")
        result[key] = value
    return result


def record(raw, entity, location, line_type=None):
    if not isinstance(raw, dict):
        raise ReferenceDataError(f"{location}: expected object")
    expected = {field.name for field in fields(entity)}
    if expected - raw.keys():
        raise ReferenceDataError(f"{location}: missing fields: " + ", ".join(sorted(expected - raw.keys())))
    if raw.keys() - expected:
        # Do not echo untrusted keys or values in diagnostics.
        raise ReferenceDataError(f"{location}: unexpected fields")
    values = {}
    for field in fields(entity):
        value = raw[field.name]
        where = f"{location}.{field.name}"
        if field.name == "lines":
            if not isinstance(value, list):
                raise ReferenceDataError(f"{where}: expected array")
            values[field.name] = tuple(record(line, line_type, f"{where}[{i}]")
                                       for i, line in enumerate(value))
            continue
        detail = None
        if field.type is str and not isinstance(value, str):
            detail = "expected string"
        elif field.type is int and type(value) is not int:
            detail = "expected integer"
        elif field.type is Decimal:
            if type(value) not in (int, Decimal) or not Decimal(value).is_finite():
                detail = "expected finite number"
            else:
                value = Decimal(value)
        elif field.type is date:
            try:
                if not isinstance(value, str) or len(value) != 10:
                    raise ValueError
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value:
                    raise ValueError
                value = parsed
            except ValueError:
                detail = "expected YYYY-MM-DD date"
        if detail:
            raise ReferenceDataError(f"{where}: {detail}")
        values[field.name] = value
    return entity(**values)


def read_reference_data(directory: Path) -> ReferenceData:
    collections = []
    for name, entity, line_type in (
        ("suppliers", Supplier, None),
        ("purchase_orders", PurchaseOrder, PurchaseOrderLine),
        ("goods_received_notes", GoodsReceivedNote, GoodsReceivedNoteLine),
    ):
        filename = f"{name}.json"
        try:
            with (Path(directory) / filename).open(encoding="utf-8") as stream:
                raw = json.load(stream, parse_float=Decimal, parse_constant=reject_constant,
                                object_pairs_hook=unique_object)
        except (OSError, UnicodeError):
            raise ReferenceDataError(f"cannot read {filename}") from None
        except (ValueError, RecursionError, DecimalException) as error:
            if isinstance(error, ReferenceDataError):
                raise ReferenceDataError(f"{filename}: {error}") from None
            raise ReferenceDataError(f"{filename}: invalid JSON") from None
        if not isinstance(raw, list):
            raise ReferenceDataError(f"{filename}: expected array")
        collections.append(tuple(record(value, entity, f"{filename}[{i}]", line_type)
                                 for i, value in enumerate(raw)))
    return ReferenceData(*collections)
