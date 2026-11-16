"""Single-snapshot PostgreSQL evaluation adapter; never authorizes payment.

Append-only is an adapter contract, not protection against database admins.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.dtos.invoice_evaluation import RuleConfigurationSnapshot
from app.application.ports.invoice_evaluation import (
    EvaluationConfigurationError, EvaluationPersistenceError,
    InvoiceConflictError, InvoiceNotFoundError,
)
from app.domain import entities
from app.domain.matching.types import InvoiceHistoryEntry, LineAssociation, MatchingConfig
from app.infrastructure.persistence import models
from app.infrastructure.persistence.evaluation_records import (
    ApprovedPaymentQuantity, EvaluationRuleFinding, InvoiceEvaluation,
    RuleConfigurationVersion, restore_evaluation,
)
from app.infrastructure.persistence.reference_data_repository import ReferenceDataTransaction, column_values


class SqlAlchemyInvoiceEvaluationTransaction:
    def __init__(self, session: Session):
        self.session = session

    def load_reference_data(self):
        return ReferenceDataTransaction(self.session).load()

    def get_invoice(self, invoice_id):
        row = self.session.get(models.Invoice, invoice_id)
        if row is None:
            return None
        values = column_values(row)
        for key in ("id", "resolved_supplier_id", "operational_state"):
            values.pop(key)
        lines = []
        for line in self.session.scalars(select(models.InvoiceLine).where(
                models.InvoiceLine.invoice_id == invoice_id).order_by(models.InvoiceLine.line_number)):
            fields = column_values(line)
            fields.pop("invoice_id")
            fields.pop("line_number")
            lines.append(entities.InvoiceLine(**fields))
        references = tuple(self.session.scalars(select(models.InvoicePOReference.po_number).where(
            models.InvoicePOReference.invoice_id == invoice_id).order_by(models.InvoicePOReference.position)))
        return entities.Invoice(**values, lines=tuple(lines), po_references=references)

    def register_invoice(self, invoice, supplier_id, invoice_id=None):
        if invoice_id is not None:
            row = self.session.get(models.Invoice, invoice_id)
            if row is None:
                raise InvoiceNotFoundError("Invoice not found")
            if (self.get_invoice(invoice_id) != invoice or
                    (supplier_id is not None and row.resolved_supplier_id not in (None, supplier_id))):
                raise InvoiceConflictError("Existing invoice conflicts with supplied input")
            if supplier_id is not None:
                row.resolved_supplier_id = supplier_id
                self.session.flush()
            return invoice_id
        values = asdict(invoice)
        values.pop("lines")
        values.pop("po_references")
        row = models.Invoice(**values, resolved_supplier_id=supplier_id)
        self.session.add(row)
        self.session.flush()
        self.session.add_all(models.InvoiceLine(invoice_id=row.id, line_number=index, **asdict(line))
                             for index, line in enumerate(invoice.lines, 1))
        self.session.add_all(models.InvoicePOReference(invoice_id=row.id, position=index, po_number=po)
                             for index, po in enumerate(invoice.po_references, 1))
        self.session.flush()
        return row.id

    def get_invoice_state(self, invoice_id):
        row = self.session.get(models.Invoice, invoice_id)
        return None if row is None else row.operational_state

    def get_configuration(self, at):
        row = self.session.scalar(select(RuleConfigurationVersion).where(
            RuleConfigurationVersion.effective_from <= at).order_by(
                RuleConfigurationVersion.effective_from.desc(), RuleConfigurationVersion.id.desc()).limit(1))
        if row is None:
            raise EvaluationConfigurationError("No applicable rule configuration")
        return RuleConfigurationSnapshot(row.id, row.effective_from,
                                         MatchingConfig(row.price_tolerance, row.analyst_approval_limit))

    def get_approved_quantities(self, po_numbers):
        ledger = ApprovedPaymentQuantity
        rows = self.session.execute(select(ledger.po_number, ledger.po_line_number, func.sum(ledger.quantity))
                                    .where(ledger.po_number.in_(po_numbers))
                                    .group_by(ledger.po_number, ledger.po_line_number))
        return {LineAssociation(po, line): quantity for po, line, quantity in rows}

    def get_invoice_history(self, exclude_invoice_id):
        rows = self.session.scalars(select(models.Invoice).where(
            models.Invoice.id != exclude_invoice_id).order_by(models.Invoice.id)).all()
        if any(row.resolved_supplier_id is None for row in rows):
            return None
        return tuple(InvoiceHistoryEntry(row.resolved_supplier_id, row.invoice_number,
                                         row.invoice_date, row.currency, row.total_amount) for row in rows)

    def append_evaluation(self, record):
        if record.evaluation_id is not None:
            raise InvoiceConflictError("Evaluation already has an ID")
        invoice = self.session.get(models.Invoice, record.invoice_id)
        if invoice is None:
            raise InvoiceNotFoundError("Invoice not found")
        result = record.result
        if result.complete != (result.final_status is not None):
            raise ValueError("Complete evaluations require a final status; incomplete evaluations must not have one")
        row = InvoiceEvaluation(
            invoice_id=record.invoice_id, configuration_id=record.configuration_id,
            price_tolerance=result.config.price_tolerance,
            analyst_approval_limit=result.config.analyst_approval_limit,
            evaluated_at=record.evaluated_at, actor_id=record.actor_id, origin=record.origin,
            provisional_status=result.provisional_status, final_status=result.final_status,
            complete=result.complete, input_snapshot=deepcopy(record.input_snapshot),
            context_snapshot=deepcopy(record.context_snapshot),
        )
        self.session.add(row)
        self.session.flush()
        self.session.add_all(EvaluationRuleFinding(evaluation_id=row.id, position=index, **asdict(rule))
                             for index, rule in enumerate(result.rules, 1))
        invoice.operational_state = record.operational_state
        self.session.flush()
        # Read the just-stored values, never today's reference/configuration facts.
        return self.get_evaluation(row.id)

    def get_evaluation(self, evaluation_id):
        row = self.session.get(InvoiceEvaluation, evaluation_id)
        if row is None:
            return None
        findings = self.session.scalars(select(EvaluationRuleFinding).where(
            EvaluationRuleFinding.evaluation_id == evaluation_id).order_by(EvaluationRuleFinding.position)).all()
        return restore_evaluation(row, findings)

    def list_evaluations(self, invoice_id):
        ids = self.session.scalars(select(InvoiceEvaluation.id).where(
            InvoiceEvaluation.invoice_id == invoice_id).order_by(InvoiceEvaluation.id))
        return tuple(self.get_evaluation(evaluation_id) for evaluation_id in ids)


class SqlAlchemyInvoiceEvaluationRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def transaction(self):
        try:
            # The connection's isolation is selected before Session.begin starts
            # any SQL. Reference and evaluation reads share this one snapshot.
            with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
                with Session(connection) as session, session.begin():
                    yield SqlAlchemyInvoiceEvaluationTransaction(session)
        except SQLAlchemyError:
            # Includes connection, statement, serialization and commit failures.
            raise EvaluationPersistenceError("Invoice evaluation persistence failed") from None
