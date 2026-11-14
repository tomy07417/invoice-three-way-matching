"""Strict file-boundary validation, independent of PostgreSQL."""
from decimal import Decimal
import json
from pathlib import Path
import shutil

import pytest

from app.application.dtos.reference_data import ReferenceDataError
from app.infrastructure.reference_data.json_reader import read_reference_data

SOURCE = Path(__file__).resolve().parents[2] / "data/reference-data/reference-data"


@pytest.fixture
def directory(tmp_path):
    for name in ("suppliers", "purchase_orders", "goods_received_notes"):
        shutil.copyfile(SOURCE / f"{name}.json", tmp_path / f"{name}.json")
    return tmp_path


def test_exact_decimal_and_domain_fields(directory):
    path = directory / "purchase_orders.json"
    path.write_text(path.read_text().replace("24.5", "24.50000000000000000001"))
    batch = read_reference_data(directory)
    assert batch.purchase_orders[0].lines[0].unit_price == Decimal("24.50000000000000000001")
    assert batch.suppliers[0].address.startswith("Kungsgatan")
    assert str(batch.goods_received_notes[0].received_date) == "2026-06-18"


@pytest.mark.parametrize("field,value,detail", [
    ("quantity_ordered", True, "expected finite number"),
    ("quantity_ordered", "1.2", "expected finite number"),
    ("unit_price", float("nan"), "nonfinite JSON number"),
    ("unit_price", float("inf"), "nonfinite JSON number"),
    ("line_number", True, "expected integer"),
    ("line_number", 1.5, "expected integer"),
    ("description", 9, "expected string"),
])
def test_invalid_line_values(directory, field, value, detail):
    path = directory / "purchase_orders.json"
    records = json.loads(path.read_text())
    records[0]["lines"][0][field] = value
    path.write_text(json.dumps(records))
    with pytest.raises(ReferenceDataError, match=detail):
        read_reference_data(directory)


@pytest.mark.parametrize("value", ["2026-02-30", "20260610", 123, None])
def test_invalid_dates(directory, value):
    path = directory / "purchase_orders.json"
    records = json.loads(path.read_text())
    records[0]["po_date"] = value
    path.write_text(json.dumps(records))
    with pytest.raises(ReferenceDataError, match="expected YYYY-MM-DD date"):
        read_reference_data(directory)


@pytest.mark.parametrize("text,detail", [
    ("[", "invalid JSON"), ("{}", "expected array"),
    ("[1]", "expected object"), ("[{}]", "missing fields"),
    ('[{"supplier_id":"one","supplier_id":"two"}]', "duplicate JSON field"),
    ('[{"number":1e9999999999999999999999999}]', "invalid JSON"),
])
def test_malformed_structure(directory, text, detail):
    (directory / "suppliers.json").write_text(text)
    with pytest.raises(ReferenceDataError, match=detail):
        read_reference_data(directory)


def test_missing_file(directory):
    with pytest.raises(ReferenceDataError, match="cannot read suppliers.json"):
        read_reference_data(directory / "missing")


def test_no_negative_or_activity_policy(directory):
    path = directory / "purchase_orders.json"
    records = json.loads(path.read_text())
    records[0]["lines"][0]["unit_price"] = -1
    records[0]["lines"][0]["quantity_ordered"] = -2
    records[0]["status"] = "closed"
    path.write_text(json.dumps(records))
    batch = read_reference_data(directory)
    assert batch.purchase_orders[0].lines[0].unit_price == Decimal(-1)
    assert batch.purchase_orders[0].lines[0].quantity_ordered == Decimal(-2)
    assert batch.suppliers[-1].status == "inactive"
