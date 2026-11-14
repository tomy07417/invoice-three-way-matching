"""Public CLI contract against isolated PostgreSQL; never inspect ORM storage."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND.parent / "data/reference-data/reference-data"
TYPES = ("suppliers", "purchase_orders", "goods_received_notes")
TOTALS = (6, 10, 11)


def output(inserted, unchanged):
    return json.dumps({name: {"inserted": i, "unchanged": u}
                       for name, i, u in zip(TYPES, inserted, unchanged)}, separators=(",", ":")) + "\n"


@pytest.fixture
def directory(tmp_path):
    for name in TYPES:
        shutil.copyfile(SOURCE / f"{name}.json", tmp_path / f"{name}.json")
    return tmp_path


@pytest.fixture
def pristine(tmp_path):
    directory = tmp_path / "pristine"
    directory.mkdir()
    for name in TYPES:
        shutil.copyfile(SOURCE / f"{name}.json", directory / f"{name}.json")
    return directory


@pytest.fixture
def cli(database_environment, alembic_cli):
    alembic_cli("upgrade", "head")

    def run(directory):
        return subprocess.run(
            [sys.executable, "-m", "app.interfaces.cli.import_reference_data", "--directory", str(directory)],
            cwd=BACKEND, env=database_environment, capture_output=True, text=True, timeout=10,
        )
    return run


def success(result, inserted, unchanged):
    assert (result.returncode, result.stdout, result.stderr) == (0, output(inserted, unchanged), "")


def test_repeat_import(cli, directory):
    success(cli(directory), TOTALS, (0, 0, 0))
    success(cli(directory), (0, 0, 0), TOTALS)
    # Numeric equivalence and line order are not changed content.
    for name in TYPES[1:]:
        path = directory / f"{name}.json"
        records = json.loads(path.read_text())
        for record in records:
            record["lines"].reverse()
        path.write_text(json.dumps(records).replace("24.5", "24.5000"))
    success(cli(directory), (0, 0, 0), TOTALS)


def failure(result, detail):
    assert (result.returncode, result.stdout, result.stderr) == (1, "", f"Import failed: {detail}\n")


def write_records(directory, name, records):
    (directory / f"{name}.json").write_text(json.dumps(records))


@pytest.mark.parametrize("case,detail", [
    ("supplier_fk", "PO references unknown supplier"),
    ("po_fk", "GRN references unknown PO"),
    ("po_line_fk", "GRN references unknown PO-local line"),
    ("supplier_duplicate", "Duplicate supplier identity"),
    ("po_duplicate", "Duplicate PO identity"),
    ("grn_duplicate", "Duplicate GRN identity"),
    ("po_line_duplicate", "Duplicate PO line identity"),
    ("grn_line_duplicate", "Duplicate GRN line identity"),
    ("malformed", "goods_received_notes.json: invalid JSON"),
    ("decimal", "goods_received_notes.json[0].lines[0].quantity_received: expected finite number"),
    ("nonfinite", "goods_received_notes.json: nonfinite JSON number"),
    ("date", "goods_received_notes.json[0].received_date: expected YYYY-MM-DD date"),
    ("field_type", "suppliers.json[0].address: expected string"),
    ("missing_file", "cannot read suppliers.json"),
])
def test_rejected_batch_is_atomic(cli, directory, pristine, tmp_path, case, detail):
    empty = tmp_path / "empty"
    empty.mkdir()
    for name in TYPES:
        write_records(empty, name, [])
    success(cli(empty), (0, 0, 0), (0, 0, 0))
    records = {name: json.loads((directory / f"{name}.json").read_text()) for name in TYPES}
    suppliers, orders, receipts = (records[name] for name in TYPES)
    if case == "supplier_fk":
        orders[-1]["supplier_id"] = "unknown"
    elif case == "po_fk":
        receipts[-1]["po_number"] = "unknown"
    elif case == "po_line_fk":
        # Line 2 exists in other POs, but not this receipt's PO.
        receipts[-1]["lines"][0]["po_line_number"] = 2
    elif case.endswith("_duplicate"):
        target = {"supplier_duplicate": suppliers, "po_duplicate": orders,
                  "grn_duplicate": receipts, "po_line_duplicate": orders[0]["lines"],
                  "grn_line_duplicate": receipts[0]["lines"]}[case]
        target.append(target[0].copy())
    elif case == "decimal":
        receipts[0]["lines"][0]["quantity_received"] = True
    elif case == "nonfinite":
        receipts[0]["lines"][0]["quantity_received"] = float("inf")
    elif case == "date":
        receipts[0]["received_date"] = "2026-02-30"
    elif case == "field_type":
        suppliers[0]["address"] = 12
    for name, values in records.items():
        write_records(directory, name, values)
    if case == "malformed":
        (directory / "goods_received_notes.json").write_text("[")
    rejected = directory / "missing" if case == "missing_file" else directory
    failure(cli(rejected), detail)
    success(cli(empty), (0, 0, 0), (0, 0, 0))
    # Repair via untouched fixture; all headers must still be new.
    success(cli(pristine), TOTALS, (0, 0, 0))
    success(cli(pristine), (0, 0, 0), TOTALS)


@pytest.mark.parametrize("name,detail", [
    ("suppliers", "supplier"), ("purchase_orders", "PO"), ("goods_received_notes", "GRN"),
])
def test_changed_content_conflicts_without_partial_additions(cli, directory, pristine, name, detail):
    success(cli(directory), TOTALS, (0, 0, 0))
    records = {key: json.loads((directory / f"{key}.json").read_text()) for key in TYPES}
    # A valid new supplier/PO/GRN accompanies conflicting old content.
    supplier = records["suppliers"][0].copy()
    supplier["supplier_id"] = "SUP-NEW"
    records["suppliers"].append(supplier)
    order = json.loads(json.dumps(records["purchase_orders"][0]))
    order.update(po_number="PO-NEW", supplier_id="SUP-NEW")
    records["purchase_orders"].append(order)
    receipt = json.loads(json.dumps(records["goods_received_notes"][0]))
    receipt.update(grn_number="GRN-NEW", po_number="PO-NEW")
    records["goods_received_notes"].append(receipt)
    repaired = json.loads(json.dumps(records))
    if name == "suppliers":
        records[name][0]["address"] = "Changed address"
    elif name == "purchase_orders":
        records[name][0]["lines"][0]["unit_price"] = 99
    else:
        records[name][0]["lines"][0]["quantity_received"] = 99
    for key, values in records.items():
        write_records(directory, key, values)
    failure(cli(directory), f"Conflicting {detail} content for existing identity")
    success(cli(pristine), (0, 0, 0), TOTALS)
    for key, values in repaired.items():
        write_records(directory, key, values)
    success(cli(directory), (1, 1, 1), TOTALS)
    success(cli(directory), (0, 0, 0), (7, 11, 12))


def test_references_can_use_persisted_data(cli, directory, pristine):
    records = {name: json.loads((directory / f"{name}.json").read_text()) for name in TYPES}
    for name in TYPES[1:]:
        write_records(directory, name, [])
    success(cli(directory), (6, 0, 0), (0, 0, 0))
    write_records(directory, "suppliers", [])
    write_records(directory, "purchase_orders", records["purchase_orders"])
    success(cli(directory), (0, 10, 0), (0, 0, 0))
    write_records(directory, "purchase_orders", [])
    write_records(directory, "goods_received_notes", records["goods_received_notes"])
    success(cli(directory), (0, 0, 11), (0, 0, 0))
    success(cli(pristine), (0, 0, 0), TOTALS)


def run_cli(*arguments, env=None):
    return subprocess.run(
        [sys.executable, "-m", "app.interfaces.cli.import_reference_data", *arguments],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=5,
    )


def test_public_help_and_missing_arguments():
    help_result = run_cli("--help")
    assert help_result.returncode == 0
    assert help_result.stderr == ""
    for text in ("--directory", "--help", "No updates/deletions", "changed content conflicts",
                 "DATABASE_URL", "inserted/unchanged"):
        assert text in " ".join(help_result.stdout.split())
    missing = run_cli()
    assert missing.returncode == 2
    assert missing.stdout == ""
    assert "the following arguments are required: --directory" in missing.stderr


@pytest.mark.parametrize("url", [None, "not-a-url-with-synthetic-secret", "sqlite:///synthetic-secret"])
def test_public_configuration_errors_are_safe(directory, url):
    env = os.environ.copy()
    for key in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        env.pop(key, None)
    if url is not None:
        env["DATABASE_URL"] = url
    result = run_cli("--directory", str(directory), env=env)
    assert (result.returncode, result.stdout, result.stderr) == (
        1, "", "Import failed: invalid or missing database configuration\n",
    )
