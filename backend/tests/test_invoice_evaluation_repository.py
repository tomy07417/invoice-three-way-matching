"""Real PostgreSQL tests through the evaluation port, without the use case."""
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.application.dtos.invoice_evaluation import EvaluationRecord
from app.application.ports.invoice_evaluation import (
    EvaluationConfigurationError, EvaluationPersistenceError,
    InvoiceConflictError, InvoiceNotFoundError,
)
from app.domain.entities import Invoice, InvoiceLine
from app.domain.matching.types import EvaluationResult, LineAssociation, RuleResult
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def invoice():
    return Invoice("Raw supplier", "tax", " Inv-01 ", date(2026, 1, 1), None,
                   ("missing", "PO", "missing"), "USD", Decimal("1.1234567890123456789"),
                   Decimal("0"), Decimal("1.1234567890123456789"),
                   (InvoiceLine("second", Decimal("1"), Decimal("1"), Decimal("1"), "raw:99"),
                    InvoiceLine("first", Decimal("2"), Decimal("3"), Decimal("6"), None)))


@pytest.fixture
def repository(migrated_engine):
    return SqlAlchemyInvoiceEvaluationRepository(migrated_engine)


def record(tx, invoice_id, pending=False):
    config = tx.get_configuration(NOW)
    rules = tuple(RuleResult(str(i), outcome, "original reason", "nonexistent", i, 99)
                  for i, outcome in enumerate(("PASS", "HOLD", "REJECT", "PENDING")
                                               if pending else ("PASS",)))
    result = EvaluationResult("Auto-Pay", None if pending else "Auto-Pay", rules, config.config)
    return EvaluationRecord(invoice_id, config.configuration_id, NOW, "actor", "test", result,
                            {"raw": ["missing", "missing"]}, {"quantity": "1.000000000001"})


def seed_reference(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO suppliers VALUES ('S', 'Supplier', 'tax', 'USD', 'active', '')"))
        c.execute(text("INSERT INTO purchase_orders VALUES ('PO', 'S', '2025-01-01', 'USD', 'open')"))
        c.execute(text("INSERT INTO purchase_order_lines VALUES ('PO', 1, 'item', 'item', 10, 1)"))


def test_repository_round_trip(repository):
    raw = invoice()
    with repository.transaction() as tx:
        invoice_id = tx.register_invoice(raw, None)
        assert tx.get_invoice(invoice_id) == raw
        assert tx.get_invoice_state(invoice_id) == "received"
        assert tx.get_invoice_history(invoice_id) == ()
        assert tx.get_approved_quantities(("PO",)) == {}
        config = tx.get_configuration(NOW)
        assert config.config.price_tolerance == Decimal("0.02")
        assert config.config.analyst_approval_limit == Decimal("5000")
        assert config.effective_from == datetime(1970, 1, 1, tzinfo=timezone.utc)
        original = tx.append_evaluation(record(tx, invoice_id, pending=True))
    with repository.transaction() as tx:
        assert tx.get_evaluation(original.evaluation_id) == original
        assert tx.list_evaluations(invoice_id) == (original,)
        assert tx.get_invoice_state(invoice_id) == "evaluation_incomplete"
        assert tx.get_invoice(invoice_id) == raw
        assert tx.get_approved_quantities(("PO",)) == {}
        complete = tx.append_evaluation(record(tx, invoice_id))
    with repository.transaction() as tx:
        assert tx.list_evaluations(invoice_id) == (original, complete)
        assert tx.get_invoice_state(invoice_id) == "evaluated"
        assert tx.get_approved_quantities(("PO",)) == {}
        with pytest.raises(InvoiceConflictError):
            tx.append_evaluation(complete)
        original.input_snapshot["raw"].append("mutated caller copy")
        assert tx.get_evaluation(original.evaluation_id).input_snapshot == {"raw": ["missing", "missing"]}
        with pytest.raises(InvoiceNotFoundError):
            tx.append_evaluation(replace(record(tx, invoice_id), invoice_id=99999))


def test_repository_rollback(repository):
    with repository.transaction() as tx:
        existing = tx.register_invoice(invoice(), None)
    with pytest.raises(RuntimeError, match="abort"):
        with repository.transaction() as tx:
            created = tx.register_invoice(invoice(), None)
            tx.append_evaluation(record(tx, existing))
            tx.append_evaluation(record(tx, created))
            raise RuntimeError("abort")
    with repository.transaction() as tx:
        assert tx.get_invoice(created) is None
        assert tx.get_invoice(existing) == invoice()
        assert tx.list_evaluations(existing) == ()
        assert tx.list_evaluations(created) == ()
        assert tx.get_invoice_state(existing) == "received"
        assert tx.get_invoice_state(created) is None
        assert tx.get_approved_quantities(("PO",)) == {}


def test_commit_failure_rolls_back(repository):
    def fail_commit(session):
        raise OperationalError("private SQL", {}, Exception("private credentials"))

    with repository.transaction() as tx:
        existing = tx.register_invoice(invoice(), None)
    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(EvaluationPersistenceError, match="^Invoice evaluation persistence failed$") as error:
            with repository.transaction() as tx:
                created = tx.register_invoice(invoice(), None)
                tx.append_evaluation(record(tx, existing))
        assert error.value.__cause__ is None
    finally:
        event.remove(Session, "before_commit", fail_commit)
    with repository.transaction() as tx:
        assert tx.get_invoice(created) is None
        assert tx.get_invoice(existing) == invoice()
        assert tx.list_evaluations(existing) == ()
        assert tx.get_invoice_state(existing) == "received"
        assert tx.get_approved_quantities(("PO",)) == {}


def test_history_and_conflicts(repository, migrated_engine):
    seed_reference(migrated_engine)
    with repository.transaction() as tx:
        first = tx.register_invoice(invoice(), "S")
        second = tx.register_invoice(invoice(), None)
        assert tx.get_invoice_history(first) is None
        assert tx.get_invoice_history(second)[0].invoice_number == " Inv-01 "
        assert tx.register_invoice(invoice(), "S", second) == second
        history = tx.get_invoice_history(first)
        assert len(history) == 1 and history[0].supplier_id == "S"
        assert history[0].total_amount == invoice().total_amount
        assert history[0].invoice_date == invoice().invoice_date
        assert history[0].currency == invoice().currency
        assert tx.register_invoice(invoice(), None, first) == first
        with pytest.raises(InvoiceConflictError):
            tx.register_invoice(replace(invoice(), invoice_number="different"), "S", first)
        with pytest.raises(InvoiceConflictError):
            tx.register_invoice(invoice(), "another", first)
        with pytest.raises(InvoiceNotFoundError):
            tx.register_invoice(invoice(), None, 99999)
        assert tx.get_invoice(99999) is None
        assert tx.get_invoice_state(99999) is None
        assert tx.get_evaluation(99999) is None


def test_config_snapshots_and_repeatable_read(repository, migrated_engine):
    with repository.transaction() as tx:
        invoice_id = tx.register_invoice(invoice(), None)
        old = tx.append_evaluation(record(tx, invoice_id))
        assert tx.load_reference_data().suppliers == ()
        with migrated_engine.begin() as c:
            c.execute(text("INSERT INTO rule_configuration_versions (effective_from, price_tolerance, analyst_approval_limit, actor_id) VALUES ('2025-01-01', 0.04, 123, 'manager')"))
            c.execute(text("INSERT INTO suppliers VALUES ('S', 'Supplier', 'tax', 'USD', 'active', '')"))
        assert tx.get_configuration(NOW).config.price_tolerance == Decimal("0.02")
        assert tx.load_reference_data().suppliers == ()
    with repository.transaction() as tx:
        assert tx.get_configuration(NOW).config.price_tolerance == Decimal("0.04")
        assert len(tx.load_reference_data().suppliers) == 1
        assert tx.get_evaluation(old.evaluation_id) == old
        assert tx.list_evaluations(invoice_id) == (old,)
        with pytest.raises(EvaluationConfigurationError):
            tx.get_configuration(datetime(1969, 1, 1, tzinfo=timezone.utc))


def test_explicit_approvals_only(repository, migrated_engine):
    seed_reference(migrated_engine)
    with repository.transaction() as tx:
        invoice_id = tx.register_invoice(invoice(), "S")
        tx.append_evaluation(record(tx, invoice_id))
        assert tx.get_approved_quantities(("PO",)) == {}
    with migrated_engine.begin() as c:
        c.execute(text("INSERT INTO approved_payment_quantities (invoice_id, po_number, po_line_number, quantity, approved_at, actor_id) VALUES (:id, 'PO', 1, 0, now(), 'manager')"), {"id": invoice_id})
    with repository.transaction() as tx:
        assert tx.get_approved_quantities(("PO",)) == {LineAssociation("PO", 1): Decimal("0")}
    with migrated_engine.begin() as c:
        c.execute(text("INSERT INTO approved_payment_quantities (invoice_id, po_number, po_line_number, quantity, approved_at, actor_id) VALUES (:id, 'PO', 1, 1.234567890123456789, now(), 'manager')"), {"id": invoice_id})
    with repository.transaction() as tx:
        assert tx.get_approved_quantities(()) == {}
        assert tx.get_approved_quantities(("unknown",)) == {}
        assert tx.get_approved_quantities(("PO",)) == {LineAssociation("PO", 1): Decimal("1.234567890123456789")}
        tx.append_evaluation(record(tx, invoice_id))
        assert tx.get_approved_quantities(("PO",)) == {LineAssociation("PO", 1): Decimal("1.234567890123456789")}


def test_safe_error_rolls_back(repository):
    with pytest.raises(EvaluationPersistenceError, match="^Invoice evaluation persistence failed$") as error:
        with repository.transaction() as tx:
            created = tx.register_invoice(invoice(), None)
            tx.register_invoice(invoice(), "nonexistent supplier")
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    with repository.transaction() as tx:
        assert tx.get_invoice(created) is None
        assert tx.list_evaluations(created) == ()
        assert tx.get_invoice_state(created) is None
        assert tx.get_approved_quantities(("PO",)) == {}
