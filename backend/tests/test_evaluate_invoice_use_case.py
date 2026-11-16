"""Application contract tests: real rules, no database or approval writer."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import inspect
import json

import pytest

from app.application.dtos.invoice_evaluation import (
    EvaluateInvoiceCommand, RuleConfigurationSnapshot,
)
from app.application.dtos.reference_data import ReferenceData
from app.application.ports.invoice_evaluation import (
    EvaluationConfigurationError, EvaluationPersistenceError, InvoiceConflictError,
)
from app.application.use_cases.evaluate_invoice import EvaluateInvoiceUseCase
from app.domain.entities import (
    GoodsReceivedNote, GoodsReceivedNoteLine, Invoice, InvoiceLine,
    PurchaseOrder, PurchaseOrderLine, Supplier,
)
from app.domain.matching.types import InvoiceHistoryEntry, LineAssociation, MatchingConfig

D = Decimal
AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
DAY = date(2026, 10, 1)
LINK = LineAssociation("PO-1", 3)


def invoice():
    return Invoice("Original Name", "raw-tax", " Inv-01 ", DAY, None, ("PO-1",),
                   "USD", D("20.00"), D("2.00"), D("22.00"),
                   (InvoiceLine("Raw item", D("2"), D("11"), D("22"), "raw-3"),))


def command(**changes):
    values = dict(invoice=invoice(), actor_id="trusted-service", supplier_id="S1",
                  associations={0: LINK}, net_unit_prices={0: D("10.00")})
    values.update(changes)
    return EvaluateInvoiceCommand(**values)


def history(number="another", total="99"):
    return InvoiceHistoryEntry("S1", number, DAY, "USD", D(total))


class FakeRepository:
    """Commit staged invoices/results only after successful context exit."""
    def __init__(self):
        supplier = Supplier("S1", "Reference name", "other-tax", "USD", "active", "Address")
        po = PurchaseOrder("PO-1", "S1", DAY, "USD", "open",
                           (PurchaseOrderLine(3, "SKU", "Item", D("10"), D("10")),))
        grn = GoodsReceivedNote("GRN-1", "PO-1", date(2026, 10, 20),
                               (GoodsReceivedNoteLine(3, D("5")),))
        self.reference = ReferenceData((supplier,), (po,), (grn,))
        self.configuration = RuleConfigurationSnapshot(7, AT, MatchingConfig())
        self.approved = {}
        self.history = ()
        self.calls = []
        self.invoices = {}
        self.records = []
        self.failure = None
        self.exit_failure = False

    @contextmanager
    def transaction(self):
        self.calls.append(("transaction",))
        invoices = dict(self.invoices)
        records = list(self.records)
        repository = self

        class Transaction:
            def load_reference_data(self):
                repository.calls.append(("load_reference_data",))
                return repository.reference

            def register_invoice(self, raw, supplier_id, invoice_id=None):
                repository.calls.append(("register_invoice", raw, supplier_id, invoice_id))
                if repository.failure:
                    raise repository.failure
                identifier = invoice_id or len(invoices) + 1
                if identifier in invoices and invoices[identifier][0] != raw:
                    raise InvoiceConflictError("Raw invoice differs")
                invoices[identifier] = (raw, supplier_id)
                return identifier

            def get_configuration(self, at):
                repository.calls.append(("get_configuration", at))
                if repository.configuration is None:
                    raise EvaluationConfigurationError("No applicable configuration")
                return repository.configuration

            def get_approved_quantities(self, po_numbers):
                repository.calls.append(("get_approved_quantities", po_numbers))
                return repository.approved

            def get_invoice_history(self, exclude_invoice_id):
                repository.calls.append(("get_invoice_history", exclude_invoice_id))
                return repository.history

            def append_evaluation(self, record):
                repository.calls.append(("append_evaluation", record))
                assert record.evaluation_id is None
                saved = replace(record, evaluation_id=len(records) + 1)
                records.append(saved)
                return saved

        try:
            yield Transaction()
            if self.exit_failure:
                raise EvaluationPersistenceError("Commit failed")
        except Exception:
            self.calls.append(("rollback",))
            raise
        else:
            self.invoices = invoices
            self.records = records
            self.calls.append(("commit",))


def execute(repository, cmd=None, clock=lambda: AT):
    return EvaluateInvoiceUseCase(repository, clock=clock).execute(cmd or command())


def test_public_usage_commits_one_evaluation_with_only_expected_calls():
    repo = FakeRepository()
    cmd = command()
    record = execute(repo, cmd)
    assert record is repo.records[0]
    assert record.evaluation_id == 1
    assert record.invoice_id == 1
    assert record.configuration_id == 7
    assert record.evaluated_at == AT
    assert (record.actor_id, record.origin) == ("trusted-service", "structured-input")
    assert record.operational_state == "evaluated"
    assert record.result.final_status == "Auto-Pay"
    assert record.result.complete and not record.result.pending
    assert record.result.config == repo.configuration.config
    assert repo.invoices == {1: (cmd.invoice, "S1")}
    assert repo.calls == [
        ("transaction",), ("load_reference_data",),
        ("register_invoice", cmd.invoice, "S1", None), ("get_configuration", AT),
        ("get_approved_quantities", ("PO-1",)), ("get_invoice_history", 1),
        ("append_evaluation", replace(record, evaluation_id=None)), ("commit",),
    ]


@pytest.mark.parametrize("case,expected", [
    ("price", "Held for Review"), ("approved", "Held for Review"),
    ("inactive", "Held for Review"), ("duplicate", "Rejected"),
    ("supplier_mismatch", "Rejected"), ("missing_po", "Rejected"),
    ("no_po", "Rejected"), ("date", "Rejected"),
    ("precedence", "Rejected"),
])
def test_real_engine_outcomes_and_rejection_precedence(case, expected):
    repo = FakeRepository()
    cmd = command()
    if case == "price":
        cmd = command(net_unit_prices={0: D("10.21")})
    elif case == "approved":
        repo.approved = {LINK: D("4")}
    elif case == "inactive":
        repo.reference = replace(repo.reference, suppliers=(replace(repo.reference.suppliers[0], status="inactive"),))
    elif case == "duplicate":
        repo.history = (history("iNV 01"),)
    elif case == "supplier_mismatch":
        repo.reference = replace(repo.reference, purchase_orders=(replace(repo.reference.purchase_orders[0], supplier_id="other"),))
    elif case == "missing_po":
        repo.reference = replace(repo.reference, purchase_orders=())
    elif case == "no_po":
        cmd = command(invoice=replace(invoice(), po_references=()))
    elif case == "date":
        cmd = command(invoice=replace(invoice(), invoice_date=date(2026, 9, 1)))
    elif case == "precedence":
        repo.approved = {LINK: D("5")}
        repo.history = (history("INV01"),)
        cmd = command(net_unit_prices={0: D("99")})
    record = execute(repo, cmd)
    assert record.result.final_status == expected
    assert record.result.complete
    assert record.operational_state == "evaluated"
    assert len(repo.records) == 1
    assert repo.calls[-1] == ("commit",)
    if case == "approved":
        assert record.context_snapshot["approved_quantities"] == [
            {"po_number": "PO-1", "po_line_number": 3, "quantity": "4"}]
        assert repo.approved == {LINK: D("4")}


@pytest.mark.parametrize("missing", ["supplier", "unknown_supplier", "case_sensitive", "price", "history", "approved"])
def test_missing_evidence_remains_incomplete(missing):
    repo = FakeRepository()
    cmd = command()
    if missing == "supplier":
        cmd = command(supplier_id=None)
    elif missing == "unknown_supplier":
        cmd = command(supplier_id="missing")
    elif missing == "case_sensitive":
        cmd = command(supplier_id="s1")
    elif missing == "price":
        cmd = command(net_unit_prices=None)
    elif missing == "history":
        repo.history = None
    else:
        repo.approved = None  # Simulate missing evidence without replacing it with zero.
    record = execute(repo, cmd)
    assert record.result.final_status is None
    assert not record.result.complete and record.result.pending
    assert record.operational_state == "evaluation_incomplete"
    assert len(repo.records) == 1
    if "supplier" in missing or missing == "case_sensitive":
        assert repo.invoices[1][1] is None
        assert record.context_snapshot["supplier"] is None
        assert record.input_snapshot["requested_supplier_id"] == cmd.supplier_id


def test_rejection_with_missing_price_is_still_incomplete_not_final_rejected():
    repo = FakeRepository()
    repo.history = (history("INV01"),)
    record = execute(repo, command(net_unit_prices=None))
    assert record.result.provisional_status == "Rejected"
    assert record.result.final_status is None
    assert record.operational_state == "evaluation_incomplete"
    assert any(rule.code == "UNIT_PRICE" for rule in record.result.pending)
    assert len(repo.records) == 1 and repo.calls[-1] == ("commit",)


def test_multiple_references_and_explicit_po_local_associations_are_not_inferred():
    repo = FakeRepository()
    second_po = replace(repo.reference.purchase_orders[0], po_number="PO-2")
    second_grn = replace(repo.reference.goods_received_notes[0], grn_number="GRN-2", po_number="PO-2")
    repo.reference = replace(repo.reference,
        purchase_orders=(*repo.reference.purchase_orders, second_po),
        goods_received_notes=(*repo.reference.goods_received_notes, second_grn))
    raw = replace(invoice(), po_references=("PO-2", "PO-1"), lines=invoice().lines * 2)
    links = {0: LINK, 1: LineAssociation("PO-2", 3)}
    prices = {0: D("10.00"), 1: D("10.00")}
    cmd = command(invoice=raw, associations=links, net_unit_prices=prices)
    # Command ownership and snapshot detachment do not rely on caller mappings.
    links.clear()
    prices.clear()
    record = execute(repo, cmd)
    assert record.result.final_status == "Auto-Pay"
    assert ("get_approved_quantities", ("PO-2", "PO-1")) in repo.calls
    assert [item["line_index"] for item in record.input_snapshot["invoice"]["lines"]] == [0, 1]
    assert record.input_snapshot["associations"] == [
        {"line_index": 0, "po_number": "PO-1", "po_line_number": 3},
        {"line_index": 1, "po_number": "PO-2", "po_line_number": 3},
    ]
    assert len(record.context_snapshot["purchase_orders"]) == 2
    assert len(record.context_snapshot["goods_received_notes"]) == 2
    assert raw.lines == invoice().lines * 2
    assert dict(cmd.associations) == {0: LINK, 1: LineAssociation("PO-2", 3)}


def test_snapshot_exact_values_indices_and_json_round_trip():
    repo = FakeRepository()
    repo.approved = {LINK: D("1.00")}
    repo.history = (history(),)
    record = execute(repo)
    expected_invoice = {
        "supplier_name": "Original Name", "supplier_tax_id": "raw-tax",
        "invoice_number": " Inv-01 ", "invoice_date": "2026-10-01", "due_date": None,
        "po_references": ["PO-1"], "currency": "USD", "subtotal": "20.00",
        "tax_amount": "2.00", "total_amount": "22.00",
        "lines": [{"line_index": 0, "description": "Raw item", "quantity": "2",
                   "unit_price": "11", "line_total": "22", "po_line_reference": "raw-3"}],
    }
    assert record.input_snapshot == {
        "invoice": expected_invoice, "requested_supplier_id": "S1",
        "associations": [{"line_index": 0, "po_number": "PO-1", "po_line_number": 3}],
        "net_unit_prices": [{"line_index": 0, "net_unit_price": "10.00"}],
    }
    assert record.context_snapshot == {
        "supplier": {"supplier_id": "S1", "legal_name": "Reference name", "tax_id": "other-tax",
                     "default_currency": "USD", "status": "active", "address": "Address"},
        "purchase_orders": [{"po_number": "PO-1", "supplier_id": "S1", "po_date": "2026-10-01",
                             "currency": "USD", "status": "open", "lines": [
                                 {"line_number": 3, "item_code": "SKU", "description": "Item",
                                  "quantity_ordered": "10", "unit_price": "10"}]}],
        "goods_received_notes": [{"grn_number": "GRN-1", "po_number": "PO-1",
                                  "received_date": "2026-10-20", "lines": [
                                      {"po_line_number": 3, "quantity_received": "5"}]}],
        "approved_quantities": [{"po_number": "PO-1", "po_line_number": 3, "quantity": "1.00"}],
        "invoice_history": [{"supplier_id": "S1", "invoice_number": "another",
                             "invoice_date": "2026-10-01", "currency": "USD", "total_amount": "99"}],
        "configuration": {"configuration_id": 7, "effective_from": AT.isoformat(),
                          "config": {"price_tolerance": "0.02", "analyst_approval_limit": "5000"}},
    }
    for snapshot in (record.input_snapshot, record.context_snapshot):
        assert json.loads(json.dumps(snapshot)) == snapshot


def test_none_and_empty_snapshots_are_distinct_and_null_association_is_explicit():
    repo = FakeRepository()
    first = execute(repo, command(associations=None, net_unit_prices=None))
    second = execute(repo, command(invoice_id=1, associations={}, net_unit_prices={}))
    third = execute(repo, command(invoice_id=1, associations={0: None}))
    assert first.input_snapshot["associations"] is None
    assert first.input_snapshot["net_unit_prices"] is None
    assert second.input_snapshot["associations"] == []
    assert second.input_snapshot["net_unit_prices"] == []
    assert third.input_snapshot["associations"] == [{"line_index": 0, "association": None}]
    assert first.context_snapshot["approved_quantities"] == []
    assert first.context_snapshot["invoice_history"] == []
    repo.approved = repo.history = None
    fourth = execute(repo, command(invoice_id=1))
    assert fourth.context_snapshot["approved_quantities"] is None
    assert fourth.context_snapshot["invoice_history"] is None


def test_repeated_evaluation_preserves_old_evidence_and_uses_current_config():
    repo = FakeRepository()
    cmd = command(net_unit_prices={0: D("10.50")})
    first = execute(repo, cmd)
    original = deepcopy((first.input_snapshot, first.context_snapshot))
    repo.configuration = RuleConfigurationSnapshot(8, AT, MatchingConfig(D("0.10"), D("9000")))
    repo.approved[LINK] = D("1")
    second = execute(repo, replace(cmd, invoice_id=first.invoice_id))
    assert first.result.final_status == "Held for Review"
    assert second.result.final_status == "Auto-Pay"
    assert second.configuration_id == 8
    assert first.configuration_id == 7
    assert (first.input_snapshot, first.context_snapshot) == original
    assert cmd.invoice == invoice() and dict(cmd.net_unit_prices) == {0: D("10.50")}
    assert first.result.config.price_tolerance == D("0.02")
    assert repo.records == [first, second]
    assert [call for call in repo.calls if call[0] == "get_invoice_history"] == [
        ("get_invoice_history", 1), ("get_invoice_history", 1)]
    assert len(repo.invoices) == 1  # No new case or approval is registered.


def test_only_referenced_orders_and_receipts_reach_engine_including_later_grns():
    repo = FakeRepository()
    unrelated_po = replace(repo.reference.purchase_orders[0], po_number="other", lines=())
    # Duplicate unrelated records would make the real engine reject its context if passed in.
    unrelated_grn = replace(repo.reference.goods_received_notes[0], grn_number="other", po_number="other")
    repo.reference = replace(repo.reference,
        purchase_orders=(*repo.reference.purchase_orders, unrelated_po, unrelated_po),
        goods_received_notes=(*repo.reference.goods_received_notes, unrelated_grn, unrelated_grn))
    record = execute(repo)
    assert record.result.final_status == "Auto-Pay"
    assert len(record.context_snapshot["purchase_orders"]) == 1
    assert len(record.context_snapshot["goods_received_notes"]) == 1
    assert record.context_snapshot["goods_received_notes"][0]["received_date"] > DAY.isoformat()


@pytest.mark.parametrize("failure", ["commit", "context", "config", "register", "clock", "clock_type", "conflict"])
def test_failure_propagates_and_rolls_back_without_returned_success(failure):
    repo = FakeRepository()
    cmd = command()
    clock = lambda: AT
    error = ValueError
    if failure == "commit":
        repo.exit_failure = True
        error = EvaluationPersistenceError
    elif failure == "context":
        cmd = command(associations={9: LINK})
    elif failure == "config":
        repo.configuration = None
        error = EvaluationConfigurationError
    elif failure == "register":
        repo.failure = EvaluationPersistenceError("Register failed")
        error = EvaluationPersistenceError
    elif failure == "clock":
        clock = lambda: datetime(2026, 1, 1)
    elif failure == "clock_type":
        clock = lambda: DAY
    else:
        execute(repo)
        cmd = command(invoice_id=1, invoice=replace(invoice(), invoice_number="changed"))
        error = InvoiceConflictError
    original_invoices, original_records = dict(repo.invoices), list(repo.records)
    with pytest.raises(error):
        execute(repo, cmd, clock)
    assert repo.invoices == original_invoices
    assert repo.records == original_records
    assert repo.calls[-1] == ("rollback",)
    assert sum(call[0] == "transaction" for call in repo.calls) == (2 if failure == "conflict" else 1)
    assert sum(call[0] == "append_evaluation" for call in repo.calls) == (
        1 if failure in ("commit", "conflict") else 0
    )


def test_default_clock_is_aware_and_application_has_no_adapter_or_framework_imports():
    repo = FakeRepository()
    record = EvaluateInvoiceUseCase(repo).execute(command())
    assert record.evaluated_at.utcoffset() is not None
    import app.application.use_cases.evaluate_invoice as module
    import app.application.dtos.evaluation_snapshots as snapshots
    for source in (module, snapshots):
        tree = ast.parse(inspect.getsource(source))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any(name and any(token in name for token in
                       ("infrastructure", "sqlalchemy", "fastapi", "psycopg")) for name in imports)
