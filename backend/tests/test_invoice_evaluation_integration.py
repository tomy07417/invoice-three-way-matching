"""Structured input through the real use case and disposable PostgreSQL ports."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.application.dtos.invoice_evaluation import EvaluateInvoiceCommand
from app.application.dtos.reference_data import ReferenceData
from app.application.ports.invoice_evaluation import EvaluationPersistenceError
from app.application.use_cases.evaluate_invoice import EvaluateInvoiceUseCase
from app.application.use_cases.import_reference_data import import_reference_data
from app.domain.entities import (
    GoodsReceivedNote, GoodsReceivedNoteLine, Invoice, InvoiceLine,
    PurchaseOrder, PurchaseOrderLine, Supplier,
)
from app.domain.matching.types import LineAssociation
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository

D = Decimal
DAY = date(2026, 10, 1)
AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
LINK = LineAssociation("PO-1", 3)


@pytest.fixture
def evaluation(migrated_engine):
    reference = ReferenceData(
        (Supplier("S1", "Reference name", "reference-tax", "USD", "active", "Address"),),
        (PurchaseOrder("PO-1", "S1", DAY, "USD", "open",
                       (PurchaseOrderLine(3, "SKU", "Item", D("10"), D("10")),)),),
        (GoodsReceivedNote("GRN-1", "PO-1", date(2026, 10, 20),
                          (GoodsReceivedNoteLine(3, D("5")),)),),
    )
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    raw = Invoice("Original Name", "raw-tax", " Inv-01 ", DAY, None, ("PO-1",),
                  "USD", D("20.00"), D("2.00"), D("22.00"),
                  (InvoiceLine("Raw item", D("2"), D("11"), D("22"), "raw-3"),))
    command = EvaluateInvoiceCommand(raw, "trusted-service", supplier_id="S1",
                                    associations={0: LINK}, net_unit_prices={0: D("10")})
    repository = SqlAlchemyInvoiceEvaluationRepository(migrated_engine)
    return repository, EvaluateInvoiceUseCase(repository, clock=lambda: AT), command


def test_structured_evaluation_round_trip(evaluation):
    repository, use_case, command = evaluation
    saved = use_case.execute(command)
    assert saved.evaluation_id is not None
    assert saved.result.complete and saved.result.final_status == "Auto-Pay"
    assert saved.result.rules and all(rule.outcome == "PASS" for rule in saved.result.rules)
    assert saved.actor_id == command.actor_id and saved.origin == "structured-input"
    assert saved.evaluated_at == AT
    assert saved.input_snapshot["invoice"]["invoice_number"] == " Inv-01 "
    assert saved.input_snapshot["invoice"]["lines"][0]["unit_price"] == "11"
    assert saved.context_snapshot["approved_quantities"] == []
    assert saved.context_snapshot["invoice_history"] == []
    assert saved.context_snapshot["goods_received_notes"][0]["received_date"] == "2026-10-20"
    assert saved.context_snapshot["configuration"]["config"]["price_tolerance"] == "0.02"
    with repository.transaction() as tx:
        assert tx.get_evaluation(saved.evaluation_id) == saved
        assert tx.list_evaluations(saved.invoice_id) == (saved,)
        assert tx.get_invoice(saved.invoice_id) == command.invoice
        assert tx.get_invoice_state(saved.invoice_id) == "evaluated"
        assert tx.get_approved_quantities(("PO-1",)) == {}


def test_readme_application_example(migrated_engine, database_environment):
    backend = Path(__file__).resolve().parents[1]
    environment = dict(database_environment, PYTHONPATH=str(backend), PYTHONDONTWRITEBYTECODE="1")
    imported = subprocess.run(
        [sys.executable, "-m", "app.interfaces.cli.import_reference_data", "--directory",
         str(backend.parent / "data/reference-data/reference-data")],
        cwd=backend, env=environment, capture_output=True, text=True, timeout=10,
    )
    assert (imported.returncode, imported.stdout, imported.stderr) == (
        0, '{"suppliers":{"inserted":6,"unchanged":0},"purchase_orders":{"inserted":10,"unchanged":0},'
           '"goods_received_notes":{"inserted":11,"unchanged":0}}\n', "")
    markdown = (backend.parent / "README.md").read_text()
    section = markdown.split("<!-- application-evaluation-example -->", 1)[1]
    script = section.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    result = subprocess.run([sys.executable, "-"], input=script, cwd=backend,
                            env=environment, capture_output=True, text=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "invoice_id=1 evaluation_id=1 status=Auto-Pay complete=True\n", "")
    repository = SqlAlchemyInvoiceEvaluationRepository(migrated_engine)
    with repository.transaction() as tx:
        saved = tx.get_evaluation(1)
        assert saved.invoice_id == 1 and saved.actor_id == "local-example"
        assert saved.result.complete and saved.result.final_status == "Auto-Pay"
        assert tx.get_invoice_state(1) == "evaluated"
        assert tx.get_approved_quantities(("PO-2026-0102",)) == {}


@pytest.mark.parametrize("case,expected,complete", [
    ("missing_net", None, False),
    ("price_hold", "Held for Review", True),
    ("duplicate", "Rejected", True),
])
def test_persisted_outcomes_and_missing_evidence(evaluation, case, expected, complete):
    repository, use_case, command = evaluation
    if case == "missing_net":
        command = replace(command, net_unit_prices=None)
    elif case == "price_hold":
        command = replace(command, net_unit_prices={0: D("10.30")})
    else:
        with repository.transaction() as tx:
            tx.register_invoice(replace(command.invoice, invoice_number="INV01"), "S1")
    saved = use_case.execute(command)
    assert saved.result.final_status == expected and saved.result.complete is complete
    if not complete:
        assert saved.input_snapshot["net_unit_prices"] is None
        assert any(rule.outcome == "PENDING" for rule in saved.result.rules)
    with repository.transaction() as tx:
        assert tx.get_evaluation(saved.evaluation_id) == saved
        assert tx.get_invoice_state(saved.invoice_id) == (
            "evaluated" if complete else "evaluation_incomplete")
        assert tx.get_approved_quantities(("PO-1",)) == {}


def test_approved_consumption_is_read_only(evaluation, migrated_engine):
    repository, use_case, command = evaluation
    with repository.transaction() as tx:
        previous = tx.register_invoice(replace(command.invoice, invoice_number="old",
                                               invoice_date=DAY - timedelta(days=1)), "S1")
    with migrated_engine.begin() as connection:
        connection.execute(text("INSERT INTO approved_payment_quantities "
            "(invoice_id, po_number, po_line_number, quantity, approved_at, actor_id) "
            "VALUES (:id, 'PO-1', 3, 4, :at, 'finance')"), {"id": previous, "at": AT})
    saved = use_case.execute(command)
    assert saved.result.complete and saved.result.final_status == "Held for Review"
    assert saved.context_snapshot["approved_quantities"] == [
        {"po_number": "PO-1", "po_line_number": 3, "quantity": "4"}]
    with repository.transaction() as tx:
        assert tx.get_approved_quantities(("PO-1",)) == {LINK: D("4")}
        assert tx.list_evaluations(previous) == ()
        assert tx.get_evaluation(saved.evaluation_id) == saved


def test_reevaluation_excludes_self_and_preserves_original_configuration(evaluation, migrated_engine):
    repository, use_case, command = evaluation
    command = replace(command, net_unit_prices={0: D("10.30")})
    first = use_case.execute(command)
    assert first.result.final_status == "Held for Review"
    future = AT + timedelta(days=1)
    with migrated_engine.begin() as connection:
        connection.execute(text("INSERT INTO rule_configuration_versions "
            "(effective_from, price_tolerance, analyst_approval_limit, actor_id) "
            "VALUES (:at, 0.05, 5000, 'policy-owner')"), {"at": future})
    # A future version does not affect today's evaluation.
    second = use_case.execute(replace(command, invoice_id=first.invoice_id))
    assert second.result == first.result and second.configuration_id == first.configuration_id
    later = EvaluateInvoiceUseCase(repository, clock=lambda: future + timedelta(days=1))
    third = later.execute(replace(command, invoice_id=first.invoice_id))
    assert third.result.final_status == "Auto-Pay" and third.result.complete
    assert third.configuration_id != first.configuration_id
    assert third.context_snapshot["invoice_history"] == []
    with repository.transaction() as tx:
        assert tx.list_evaluations(first.invoice_id) == (first, second, third)
        assert tx.get_evaluation(first.evaluation_id) == first
        assert tx.get_approved_quantities(("PO-1",)) == {}


def test_unresolved_other_case_is_not_empty_history(evaluation):
    repository, use_case, command = evaluation
    with repository.transaction() as tx:
        tx.register_invoice(replace(command.invoice, invoice_number="unresolved"), None)
    saved = use_case.execute(command)
    assert saved.context_snapshot["invoice_history"] is None
    assert not saved.result.complete and saved.result.final_status is None
    with repository.transaction() as tx:
        assert tx.get_evaluation(saved.evaluation_id) == saved
        assert tx.get_invoice_state(saved.invoice_id) == "evaluation_incomplete"
        assert tx.get_approved_quantities(("PO-1",)) == {}


def test_commit_failure_does_not_return_or_persist_evaluation(evaluation):
    repository, use_case, command = evaluation
    with repository.transaction() as tx:
        invoice_id = tx.register_invoice(command.invoice, "S1")

    def fail_commit(session):
        raise OperationalError("private SQL", {}, Exception("private connection credentials"))

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(EvaluationPersistenceError) as error:
            use_case.execute(replace(command, invoice_id=invoice_id))
        assert str(error.value) == "Invoice evaluation persistence failed"
        assert error.value.__cause__ is None
    finally:
        event.remove(Session, "before_commit", fail_commit)
    with repository.transaction() as tx:
        assert tx.list_evaluations(invoice_id) == ()
        assert tx.get_invoice(invoice_id) == command.invoice
        assert tx.get_invoice_state(invoice_id) == "received"
        assert tx.get_approved_quantities(("PO-1",)) == {}
