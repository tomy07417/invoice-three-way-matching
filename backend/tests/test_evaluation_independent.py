"""Regression probes promoted from independent migration/snapshot verification."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.application.dtos.reference_data import ReferenceData
from app.application.ports.invoice_evaluation import InvoiceConflictError
from app.application.use_cases.import_reference_data import import_reference_data
from app.domain.entities import GoodsReceivedNote, GoodsReceivedNoteLine, Invoice, InvoiceLine
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from backend.tests.test_invoice_evaluation_integration import evaluation, AT, LINK


def observable_state(repository, invoice_id):
    with repository.transaction() as transaction:
        return (
            transaction.get_invoice(invoice_id),
            transaction.list_evaluations(invoice_id),
            transaction.get_invoice_state(invoice_id),
            transaction.get_approved_quantities(("PO-1",)),
            transaction.get_invoice_history(invoice_id),
        )


def test_rejected_commands_preserve_public_state(evaluation, capsys):
    repository, use_case, command = evaluation
    saved = use_case.execute(command)
    for bad, error, message in (
        (replace(command, associations={1: None}), ValueError,
         "Invoice line index is outside evaluation context"),
        (replace(command, invoice_id=saved.invoice_id,
                 invoice=replace(command.invoice, total_amount=command.invoice.total_amount + 1)),
         InvoiceConflictError, "Existing invoice conflicts with supplied input"),
    ):
        before = observable_state(repository, saved.invoice_id)
        with pytest.raises(error) as caught:
            use_case.execute(bad)
        assert str(caught.value) == message
        assert observable_state(repository, saved.invoice_id) == before
        assert capsys.readouterr() == ("", "")


def test_upgrade_preserves_duplicate_raw_rows_and_constraints(database_url, alembic_cli):
    alembic_cli("upgrade", "0001")
    engine = create_engine(database_url)

    def constraints():
        # Migration schema is the contract under test, not application storage.
        with engine.connect() as connection:
            return set(connection.execute(text(
                "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE connamespace='public'::regnamespace")).all())

    expected = Invoice("raw", "tax", "duplicate", date(2020, 1, 1), None,
                       ("unknown", "unknown"), "USD", Decimal("1.001"), Decimal("0"),
                       Decimal("1.001"), (InvoiceLine("raw", Decimal("1"), Decimal("1.001"),
                                                     Decimal("1.001"), "unknown:99"),))
    try:
        ids = []
        with engine.begin() as connection:
            for _ in range(2):
                invoice_id = connection.scalar(text(
                    "INSERT INTO invoices (supplier_name,supplier_tax_id,invoice_number,invoice_date,"
                    "currency,subtotal,tax_amount,total_amount) "
                    "VALUES ('raw','tax','duplicate','2020-01-01','USD',1.001,0,1.001) RETURNING id"))
                ids.append(invoice_id)
                connection.execute(text("INSERT INTO invoice_po_references "
                    "VALUES (:id,0,'unknown'),(:id,3,'unknown')"), {"id": invoice_id})
                connection.execute(text("INSERT INTO invoice_lines "
                    "VALUES (:id,7,'raw',1,1.001,1.001,'unknown:99')"), {"id": invoice_id})
        original = constraints()
        alembic_cli("upgrade", "head")
        assert original <= constraints()
        with SqlAlchemyInvoiceEvaluationRepository(engine).transaction() as transaction:
            for invoice_id in ids:
                assert transaction.get_invoice(invoice_id) == expected
                assert transaction.get_invoice_state(invoice_id) == "received"
                assert transaction.list_evaluations(invoice_id) == ()
            assert transaction.get_approved_quantities(("unknown",)) == {}
    finally:
        engine.dispose()


def test_history_ledger_and_grns_share_snapshot(evaluation, migrated_engine):
    repository, _, command = evaluation
    with repository.transaction() as transaction:
        previous = transaction.register_invoice(replace(command.invoice, invoice_number="previous"), "S1")
    extra = GoodsReceivedNote("GRN-2", "PO-1", date(2026, 10, 25),
                              (GoodsReceivedNoteLine(3, Decimal("2")),))
    with repository.transaction() as transaction:
        assert transaction.get_approved_quantities(("PO-1",)) == {}
        old_reference = transaction.load_reference_data()
        old_history = transaction.get_invoice_history(previous)
        with migrated_engine.begin() as connection:
            connection.execute(text("INSERT INTO approved_payment_quantities "
                "(invoice_id,po_number,po_line_number,quantity,approved_at,actor_id) "
                "VALUES (:id,'PO-1',3,1,:at,'finance')"), {"id": previous, "at": AT})
        import_reference_data(ReferenceData((), (), (extra,)), SqlReferenceDataRepository(migrated_engine))
        with repository.transaction() as other:
            other.register_invoice(replace(command.invoice, invoice_number="later"), "S1")
        assert transaction.get_approved_quantities(("PO-1",)) == {}
        assert transaction.load_reference_data() == old_reference
        assert transaction.get_invoice_history(previous) == old_history
    with repository.transaction() as transaction:
        assert transaction.get_approved_quantities(("PO-1",)) == {LINK: Decimal("1")}
        current = transaction.load_reference_data()
        assert current.suppliers == old_reference.suppliers
        assert current.purchase_orders == old_reference.purchase_orders
        assert set(current.goods_received_notes) == set(old_reference.goods_received_notes) | {extra}
        assert len(transaction.get_invoice_history(previous)) == len(old_history) + 1
