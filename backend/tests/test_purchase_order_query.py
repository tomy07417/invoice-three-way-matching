"""Public HTTP queries seeded only through the public importer CLI."""
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND.parent / "data/reference-data/reference-data"
TYPES = ("suppliers", "purchase_orders", "goods_received_notes")


def request(base, path):
    try:
        response = urlopen(base + path, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers.get_content_type(), json.load(response)


@contextmanager
def server(environment):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            assert process.poll() is None, "API exited during startup"
            try:
                request(base, "/health")
                break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("API did not start within 8 seconds")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


@pytest.fixture
def query_api(database_environment, alembic_cli, tmp_path):
    alembic_cli("upgrade", "head")
    for name in TYPES:
        shutil.copyfile(SOURCE / f"{name}.json", tmp_path / f"{name}.json")

    def import_data():
        return subprocess.run(
            [sys.executable, "-m", "app.interfaces.cli.import_reference_data", "--directory", str(tmp_path)],
            cwd=BACKEND, env=database_environment, capture_output=True, text=True, timeout=10,
        )

    result = import_data()
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == {
        name: {"inserted": count, "unchanged": 0} for name, count in zip(TYPES, (6, 10, 11))
    }
    with server(database_environment) as base:
        yield base, tmp_path, import_data


def expected_order(number, received):
    orders = json.loads((SOURCE / "purchase_orders.json").read_text(), parse_float=Decimal)
    order = next(order for order in orders if order["po_number"] == number)
    for line, quantity in zip(order["lines"], received):
        for key in ("quantity_ordered", "unit_price"):
            line[key] = str(line[key])
        line["quantity_received"] = quantity
    return order


def test_query_original_po0102(query_api):
    base, _, _ = query_api
    assert request(base, "/purchase-orders/PO-2026-0102") == (
        200, "application/json", expected_order("PO-2026-0102", ["15", "30"]),
    )


@pytest.mark.parametrize("number,received", [("0107", ["8"]), ("0101", ["40", "6"]), ("0109", ["800"])])
def test_partial_and_po_local_quantities(query_api, number, received):
    base, _, _ = query_api
    number = "PO-2026-" + number
    assert request(base, "/purchase-orders/" + number) == (
        200, "application/json", expected_order(number, received),
    )


def test_zero_receipts_ordering_and_exact_decimals(query_api):
    base, directory, run_import = query_api
    path = directory / "purchase_orders.json"
    orders = json.loads(path.read_text())
    order = json.loads(json.dumps(orders[1]))
    order["po_number"] = "PO-UNRECEIVED"
    order["lines"].reverse()
    # Import only the added PO; raw literals retain precision beyond a binary float.
    encoded = (json.dumps([order]).replace('185.0', '185.1234567890123456789')
               .replace('12.0', '12.0000000000000000001')
               .replace('15,', '15.1234567890123456789,'))
    path.write_text(encoded)
    added = json.loads(encoded, parse_float=str)[0]
    result = run_import()
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == {
        "suppliers": {"inserted": 0, "unchanged": 6},
        "purchase_orders": {"inserted": 1, "unchanged": 0},
        "goods_received_notes": {"inserted": 0, "unchanged": 11},
    }
    for line in added["lines"]:
        line["quantity_ordered"] = str(line["quantity_ordered"])
        line["unit_price"] = str(line["unit_price"])
        line["quantity_received"] = "0"
    added["lines"].sort(key=lambda line: line["line_number"])
    assert request(base, "/purchase-orders/PO-UNRECEIVED") == (200, "application/json", added)
    receipt = {
        "grn_number": "GRN-EXACT", "po_number": "PO-UNRECEIVED", "received_date": "2026-06-29",
        "lines": [{"po_line_number": 1, "quantity_received": 1}],
    }
    (directory / "goods_received_notes.json").write_text(
        json.dumps([receipt]).replace('"quantity_received": 1', '"quantity_received": 0.1234567890123456789')
    )
    result = run_import()
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == {
        "suppliers": {"inserted": 0, "unchanged": 6},
        "purchase_orders": {"inserted": 0, "unchanged": 1},
        "goods_received_notes": {"inserted": 1, "unchanged": 0},
    }
    added["lines"][0]["quantity_received"] = "0.1234567890123456789"
    assert request(base, "/purchase-orders/PO-UNRECEIVED") == (200, "application/json", added)


def test_unknown_and_sql_like_identity(query_api):
    base, _, _ = query_api
    for number in ("unknown", "' OR '1'='1"):
        assert request(base, "/purchase-orders/" + quote(number, safe="")) == (
            404, "application/json", {"detail": "Purchase order not found"},
        )
    assert request(base, "/purchase-orders/PO-2026-0102")[0] == 200


def test_reimport_and_rejected_batch_preserve_query(query_api):
    base, directory, run_import = query_api
    before = request(base, "/purchase-orders/PO-2026-0102")
    result = run_import()
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == {
        name: {"inserted": 0, "unchanged": count} for name, count in zip(TYPES, (6, 10, 11))
    }
    assert request(base, "/purchase-orders/PO-2026-0102") == before
    path = directory / "goods_received_notes.json"
    receipts = json.loads(path.read_text())
    receipts[-1]["lines"][0]["po_line_number"] = 999
    path.write_text(json.dumps(receipts))
    result = run_import()
    assert (result.returncode, result.stdout, result.stderr) == (
        1, "", "Import failed: Conflicting GRN content for existing identity\n",
    )
    assert request(base, "/purchase-orders/PO-2026-0102") == before
    shutil.copyfile(SOURCE / "goods_received_notes.json", path)
    result = run_import()
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == {
        name: {"inserted": 0, "unchanged": count} for name, count in zip(TYPES, (6, 10, 11))
    }
