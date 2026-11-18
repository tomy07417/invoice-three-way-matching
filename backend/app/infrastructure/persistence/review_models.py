"""Review state pointers and append-only extraction, decision and reversal facts."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base
from app.infrastructure.persistence.processing_models import UploadCase  # noqa: F401


class ReviewCase(Base):
    __tablename__ = 'review_cases'
    __table_args__ = (CheckConstraint('revision >= 0 AND extraction_version >= 1', name='ck_review_revision'),)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('upload_cases.invoice_id'), primary_key=True)
    revision: Mapped[int]
    extraction_version: Mapped[int]
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey('invoice_evaluations.id'))
    current_decision_id: Mapped[int | None]  # Pointer maintained under the case lock; history is never deleted.
    operational_state: Mapped[str]
    payment_blocked: Mapped[bool]
    credit_note_pending: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewExtractionVersion(Base):
    __tablename__ = 'review_extraction_versions'
    __table_args__ = (CheckConstraint('number >= 1', name='ck_review_extraction_number'),)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('review_cases.invoice_id'), primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    draft: Mapped[dict | None] = mapped_column(JSONB)
    raw_invoice_id: Mapped[int | None] = mapped_column(ForeignKey('invoices.id'), index=True)
    actor_id: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str]


class ReviewEvaluationLink(Base):
    __tablename__ = 'review_evaluation_links'
    evaluation_id: Mapped[int] = mapped_column(ForeignKey('invoice_evaluations.id'), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('review_cases.invoice_id'), index=True)
    extraction_version: Mapped[int]


class ReviewDecision(Base):
    __tablename__ = 'review_decisions'
    __table_args__ = (UniqueConstraint('invoice_id', 'revision', name='uq_review_decision_revision'),)
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('review_cases.invoice_id'), index=True)
    revision: Mapped[int]
    extraction_version: Mapped[int]
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey('invoice_evaluations.id'))
    action: Mapped[str]
    actor_id: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str]


class ReviewApprovalLink(Base):
    __tablename__ = 'review_approval_links'
    ledger_id: Mapped[int] = mapped_column(ForeignKey('approved_payment_quantities.id'), primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey('review_decisions.id'), index=True)


class ReviewApprovalReversal(Base):
    __tablename__ = 'review_approval_reversals'
    decision_id: Mapped[int] = mapped_column(ForeignKey('review_decisions.id'), primary_key=True)
    actor_id: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str]


class ReviewAuditEvent(Base):
    __tablename__ = 'review_audit_events'
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('review_cases.invoice_id'), index=True)
    revision: Mapped[int]
    kind: Mapped[str]
    actor_id: Mapped[str]
    actor_name: Mapped[str]
    actor_role: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB)
