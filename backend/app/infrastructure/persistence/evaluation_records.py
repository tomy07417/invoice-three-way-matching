"""Evaluation SQL records and lossless reconstruction from stored evidence."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Identity, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.application.dtos.invoice_evaluation import EvaluationRecord
from app.domain.matching.types import EvaluationResult, MatchingConfig, RuleResult
from app.infrastructure.persistence.models import Base


class RuleConfigurationVersion(Base):
    __tablename__ = "rule_configuration_versions"
    __table_args__ = (CheckConstraint(
        "price_tolerance >= 0 AND analyst_approval_limit >= 0",
        name="ck_configuration_nonnegative"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_tolerance: Mapped[Decimal] = mapped_column(Numeric())
    analyst_approval_limit: Mapped[Decimal] = mapped_column(Numeric())
    actor_id: Mapped[str]


class ApprovedPaymentQuantity(Base):
    """Explicit approval facts only; evaluation never writes these records."""
    __tablename__ = "approved_payment_quantities"
    __table_args__ = (ForeignKeyConstraint(
        ["po_number", "po_line_number"],
        ["purchase_order_lines.po_number", "purchase_order_lines.line_number"],
        name="fk_approval_po_line"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    po_number: Mapped[str]
    po_line_number: Mapped[int]
    quantity: Mapped[Decimal] = mapped_column(Numeric())
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str]


class InvoiceEvaluation(Base):
    __tablename__ = "invoice_evaluations"
    __table_args__ = (
        CheckConstraint("provisional_status IN ('Auto-Pay', 'Rejected', 'Held for Review')", name="ck_evaluation_provisional"),
        CheckConstraint("final_status IS NULL OR final_status IN ('Auto-Pay', 'Rejected', 'Held for Review')", name="ck_evaluation_final"),
        CheckConstraint("(complete AND final_status IS NOT NULL AND final_status = provisional_status) OR (NOT complete AND final_status IS NULL)", name="ck_evaluation_completeness"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    configuration_id: Mapped[int] = mapped_column(ForeignKey("rule_configuration_versions.id"))
    price_tolerance: Mapped[Decimal] = mapped_column(Numeric())
    analyst_approval_limit: Mapped[Decimal] = mapped_column(Numeric())
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str]
    origin: Mapped[str]
    provisional_status: Mapped[str]
    final_status: Mapped[str | None]
    complete: Mapped[bool]
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    context_snapshot: Mapped[dict] = mapped_column(JSONB)


class EvaluationRuleFinding(Base):
    __tablename__ = "evaluation_rule_findings"
    __table_args__ = (CheckConstraint(
        "outcome IN ('PASS', 'HOLD', 'REJECT', 'PENDING')", name="ck_finding_outcome"),)

    evaluation_id: Mapped[int] = mapped_column(ForeignKey("invoice_evaluations.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str]
    outcome: Mapped[str]
    reason: Mapped[str]
    po_number: Mapped[str | None]
    line_index: Mapped[int | None]
    po_line_number: Mapped[int | None]


def restore_evaluation(row: InvoiceEvaluation, findings: list[EvaluationRuleFinding]) -> EvaluationRecord:
    config = MatchingConfig(row.price_tolerance, row.analyst_approval_limit)
    rules = tuple(RuleResult(f.code, f.outcome, f.reason, f.po_number,
                             f.line_index, f.po_line_number) for f in findings)
    return EvaluationRecord(
        invoice_id=row.invoice_id, configuration_id=row.configuration_id,
        evaluated_at=row.evaluated_at, actor_id=row.actor_id, origin=row.origin,
        result=EvaluationResult(row.provisional_status, row.final_status, rules, config),
        input_snapshot=deepcopy(row.input_snapshot), context_snapshot=deepcopy(row.context_snapshot),
        evaluation_id=row.id,
    )
