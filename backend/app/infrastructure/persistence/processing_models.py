"""Upload cases exist independently of mandatory raw invoice fields."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base
from app.infrastructure.persistence.auth_models import User  # noqa: F401 -- shared FK metadata


class ProcessingControl(Base):
    __tablename__ = 'processing_control'
    __table_args__ = (CheckConstraint('id = 1', name='ck_processing_single_control'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    configuration: Mapped[dict | None] = mapped_column(JSONB)


class UploadCase(Base):
    __tablename__ = 'upload_cases'
    __table_args__ = (
        UniqueConstraint('owner_id', 'idempotency_key', name='uq_upload_owner_key'),
        CheckConstraint("state IN ('QUEUED','EXTRACTING','EXTRACTED','EVALUATING','COMPLETED',"
                        "'RETRY_WAIT','EXTRACTION_FAILED','EVALUATION_INCOMPLETE','PROCESSING_FAILED')",
                        name='ck_processing_state'),
        CheckConstraint('attempts >= 0 AND deferrals >= 0 AND version >= 0 AND estimated_tokens > 0',
                        name='ck_processing_counters'),
    )
    invoice_id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    idempotency_key: Mapped[str]
    storage_key: Mapped[str]
    document_metadata: Mapped[dict] = mapped_column(JSONB)
    estimated_tokens: Mapped[int]
    state: Mapped[str] = mapped_column(index=True)
    attempts: Mapped[int]
    deferrals: Mapped[int]
    version: Mapped[int]
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None]
    extraction: Mapped[dict | None] = mapped_column(JSONB)
    raw_invoice_id: Mapped[int | None] = mapped_column(ForeignKey('invoices.id'))
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey('invoice_evaluations.id'))


class ProcessingAttempt(Base):
    __tablename__ = 'processing_attempts'
    __table_args__ = (CheckConstraint('reserved_tokens > 0 AND number > 0', name='ck_attempt_budget'),)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('upload_cases.invoice_id'), primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[UUID]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reserved_tokens: Mapped[int]
    reported_tokens: Mapped[int | None]
    error_code: Mapped[str | None]


class ProcessingEvent(Base):
    __tablename__ = 'processing_events'
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    invoice_id: Mapped[UUID] = mapped_column(ForeignKey('upload_cases.invoice_id'), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str]
    error_code: Mapped[str | None]
