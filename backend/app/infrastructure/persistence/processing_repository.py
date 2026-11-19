"""PostgreSQL durable jobs; one locked row serializes admission and budgets.

Every method closes its short transaction before returning. Reservations are
charged for a rolling window after completion (or while active), never released
on unknown usage. Reported usage can increase, never decrease, the estimate.
The worker must estimate *total* input plus maximum output before claiming.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import random as randomness
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.dtos.auth import Role
from app.application.dtos.accounting import parse_accounting_evidence
from app.application.dtos.invoice_documents import (
    DocumentMetadata, ExtractionResult, InvoiceDraft, InvoiceLineDraft, TokenUsage,
)
from app.application.dtos.processing import ProcessingClaim, ProcessingConfig, ProcessingJob, ProcessingState
from app.application.ports.processing import AdmissionRejected, StaleLease
from app.application.use_cases.retry_policy import RetryPolicy
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationTransaction
from app.infrastructure.persistence.processing_models import (
    ProcessingAttempt, ProcessingControl, ProcessingEvent, UploadCase,
)

PENDING = ('QUEUED', 'EXTRACTING', 'RETRY_WAIT', 'EXTRACTED', 'EVALUATING')
SAFE_CODES = frozenset(('timeout', 'rate_limited', 'provider_unavailable', 'authentication_failed',
                        'request_rejected', 'unsupported_document', 'invalid_response',
                        'lease_expired', 'token_budget', 'processing_failed'))


def _json_result(result):
    # JSON contains exact Decimal strings and ISO dates, never binary floats.
    def encode(value):
        if isinstance(value, (Decimal, date)):
            return str(value)
        raise TypeError('Unsupported extraction value')
    value = asdict(result)
    if value['invoice'] is not None and value['invoice'].get('accounting_evidence') is None:
        value['invoice'].pop('accounting_evidence', None)
    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def _restore(value):
    if value is None:
        return None
    invoice = value['invoice']
    if invoice is not None:
        invoice = dict(invoice)
        for key in ('subtotal', 'tax_amount', 'total_amount'):
            invoice[key] = Decimal(invoice[key]) if invoice[key] is not None else None
        for key in ('invoice_date', 'due_date'):
            invoice[key] = date.fromisoformat(invoice[key]) if invoice[key] is not None else None
        if invoice['po_references'] is not None:
            invoice['po_references'] = tuple(invoice['po_references'])
        lines = []
        for line in invoice['lines']:
            line = dict(line)
            for key in ('quantity', 'unit_price', 'line_total'):
                line[key] = Decimal(line[key]) if line[key] is not None else None
            lines.append(InvoiceLineDraft(**line))
        invoice['lines'] = tuple(lines)
        invoice['accounting_evidence'] = parse_accounting_evidence(invoice.get('accounting_evidence'))
        invoice = InvoiceDraft(**invoice)
    usage = TokenUsage(**value['usage']) if value['usage'] is not None else None
    return ExtractionResult(value['is_invoice'], invoice, tuple(value['warnings']), usage, value['response_id'])


class SqlProcessingRepository:
    def __init__(self, engine, *, config=ProcessingConfig(), retry_policy=RetryPolicy(),
                 clock=lambda: datetime.now(timezone.utc), random=randomness.SystemRandom().random):
        self.engine = engine
        self.config = config
        self.retry_policy = retry_policy
        self.clock = clock
        self.random = random

    @contextmanager
    def _transaction(self):
        with Session(self.engine) as session, session.begin():
            yield session

    def _lock(self, session):
        control = session.scalar(select(ProcessingControl).where(ProcessingControl.id == 1).with_for_update())
        if control is None:
            raise RuntimeError('Processing migration required')
        configuration = dict(processing=asdict(self.config), retry=asdict(self.retry_policy))
        if control.configuration is None:
            control.configuration = configuration
        elif control.configuration != configuration:
            raise ValueError('Workers must share processing configuration')

    def _now(self):
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Clock must be timezone aware')
        return now

    def _event(self, session, row, state, now, code=None):
        row.state = str(state)
        row.updated_at = now
        row.error_code = code
        session.add(ProcessingEvent(invoice_id=row.invoice_id, at=now, state=str(state), error_code=code))
        session.flush()

    def _snapshot(self, session, row):
        attempts = session.scalars(select(ProcessingAttempt).where(
            ProcessingAttempt.invoice_id == row.invoice_id).order_by(ProcessingAttempt.number)).all()
        events = session.scalars(select(ProcessingEvent).where(
            ProcessingEvent.invoice_id == row.invoice_id).order_by(ProcessingEvent.id)).all()
        return ProcessingJob(
            row.invoice_id, row.job_id, row.owner_id, row.storage_key,
            DocumentMetadata(**row.document_metadata), ProcessingState(row.state), row.attempts,
            row.next_eligible_at, row.created_at, row.updated_at, row.error_code,
            _restore(row.extraction), row.raw_invoice_id, row.evaluation_id,
            tuple(dict(number=a.number, started_at=a.started_at, finished_at=a.finished_at,
                       reserved_tokens=a.reserved_tokens, reported_tokens=a.reported_tokens,
                       error_code=a.error_code) for a in attempts),
            tuple(dict(at=e.at, state=e.state, error_code=e.error_code) for e in events))

    def admit(self, actor, idempotency_key, storage_key, metadata, estimated_tokens):
        if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200
                or not idempotency_key.strip() or not isinstance(storage_key, str)
                or not storage_key.strip() or len(storage_key) > 500
                or not isinstance(metadata, DocumentMetadata)
                or metadata.media_type not in ('application/pdf', 'image/png', 'image/jpeg')
                or type(metadata.byte_size) is not int or metadata.byte_size <= 0
                or type(metadata.page_count) is not int or metadata.page_count <= 0
                or len(metadata.sha256) != 64 or any(c not in '0123456789abcdef' for c in metadata.sha256)
                or type(estimated_tokens) is not int or not 0 < estimated_tokens <= self.config.token_limit):
            raise ValueError('Invalid processing admission')
        with self._transaction() as session:
            self._lock(session)
            now = self._now()
            existing = session.scalar(select(UploadCase).where(
                UploadCase.owner_id == actor.actor_id, UploadCase.idempotency_key == idempotency_key))
            if existing is not None:
                return self._snapshot(session, existing)
            count = session.scalar(select(func.count()).select_from(UploadCase).where(UploadCase.state.in_(PENDING)))
            if count >= self.config.max_pending:
                raise AdmissionRejected('Processing queue is full')
            row = UploadCase(invoice_id=uuid4(), job_id=uuid4(), owner_id=actor.actor_id,
                             idempotency_key=idempotency_key, storage_key=storage_key,
                             document_metadata=asdict(metadata), estimated_tokens=estimated_tokens,
                             state='QUEUED', attempts=0, deferrals=0, version=0,
                             next_eligible_at=now, created_at=now, updated_at=now)
            session.add(row)
            session.flush()
            self._event(session, row, 'QUEUED', now)
            return self._snapshot(session, row)

    def _visible(self, actor):
        query = select(UploadCase)
        if actor.role not in (Role.AP_ANALYST, Role.AP_MANAGER):
            query = query.where(UploadCase.owner_id == actor.actor_id)
        return query

    def get(self, actor, invoice_id):
        with self._transaction() as session:
            row = session.scalar(self._visible(actor).where(UploadCase.invoice_id == invoice_id))
            return self._snapshot(session, row) if row else None

    def list(self, actor):
        with self._transaction() as session:
            return tuple(self._snapshot(session, row) for row in session.scalars(
                self._visible(actor).order_by(UploadCase.created_at, UploadCase.invoice_id)))

    def _retry(self, session, row, now, code, retryable=True, floor=None):
        next_at = self.retry_policy.next_at(now, row.attempts, random_value=self.random(),
                                            retryable=retryable, floor=floor)
        row.lease_token = None
        row.lease_until = None
        if next_at is None:
            self._event(session, row, 'EXTRACTION_FAILED', now, code)
        else:
            row.next_eligible_at = next_at
            self._event(session, row, 'RETRY_WAIT', now, code)

    def _recover(self, session, now):
        expired = session.scalars(select(UploadCase).where(
            UploadCase.state.in_(('EXTRACTING', 'EVALUATING')), UploadCase.lease_until <= now))
        for row in expired:
            if row.state == 'EXTRACTING':
                attempt = session.get(ProcessingAttempt, (row.invoice_id, row.attempts))
                attempt.finished_at = now
                attempt.error_code = 'lease_expired'
                self._retry(session, row, now, 'lease_expired')
            else:
                # Evaluation is atomic; an expired uncommitted attempt has no
                # saved result. Fail explicitly rather than loop indefinitely.
                row.lease_token = None
                row.lease_until = None
                self._event(session, row, 'PROCESSING_FAILED', now, 'lease_expired')
        session.flush()

    def _lease(self, session, row, now, state):
        row.version += 1
        row.lease_token = uuid4()
        row.lease_until = now + timedelta(seconds=self.config.lease_seconds)
        self._event(session, row, state, now)
        return ProcessingClaim(self._snapshot(session, row), row.lease_token, row.version, row.lease_until)

    def claim(self):
        with self._transaction() as session:
            self._lock(session)
            now = self._now()
            self._recover(session, now)
            active = session.scalar(select(func.count()).select_from(UploadCase).where(UploadCase.state == 'EXTRACTING'))
            if active >= self.config.max_active:
                return None
            row = session.scalar(select(UploadCase).where(
                UploadCase.state.in_(('QUEUED', 'RETRY_WAIT')), UploadCase.next_eligible_at <= now
            ).order_by(UploadCase.created_at, UploadCase.invoice_id).limit(1).with_for_update(skip_locked=True))
            if row is None:
                return None
            if row.attempts >= self.retry_policy.max_attempts:
                self._event(session, row, 'EXTRACTION_FAILED', now, 'processing_failed')
                return None
            cutoff = now - timedelta(seconds=self.config.window_seconds)
            charged = session.scalars(select(ProcessingAttempt).where(
                (ProcessingAttempt.finished_at.is_(None)) | (ProcessingAttempt.finished_at > cutoff))).all()
            total = sum(max(a.reserved_tokens, a.reported_tokens or 0) for a in charged)
            if total + row.estimated_tokens > self.config.token_limit:
                # All unfinished calls keep their reservation; reopen no earlier
                # than a full window from now if the first release is unknown.
                reopen = min((a.finished_at or now) + timedelta(seconds=self.config.window_seconds) for a in charged)
                row.deferrals += 1
                delay = self.retry_policy.delay(row.deferrals, random_value=self.random(),
                                                minimum_seconds=max(0, (reopen - now).total_seconds()))
                row.next_eligible_at = now + timedelta(seconds=delay)
                self._event(session, row, 'RETRY_WAIT', now, 'token_budget')
                return None
            row.attempts += 1
            claim = self._lease(session, row, now, 'EXTRACTING')
            session.add(ProcessingAttempt(invoice_id=row.invoice_id, number=row.attempts,
                                          token=claim.token, started_at=now, reserved_tokens=row.estimated_tokens))
            session.flush()
            return ProcessingClaim(self._snapshot(session, row), claim.token, claim.version, claim.expires_at)

    def _fence(self, session, claim, now, state):
        row = session.scalar(select(UploadCase).where(UploadCase.invoice_id == claim.job.invoice_id).with_for_update())
        if (row is None or row.state != state or row.lease_token != claim.token
                or row.version != claim.version or row.lease_until is None or row.lease_until <= now):
            raise StaleLease('Processing lease is no longer current')
        return row

    def extracted(self, claim, result):
        encoded = _json_result(result)
        with self._transaction() as session:
            self._lock(session)
            now = self._now()
            row = self._fence(session, claim, now, 'EXTRACTING')
            attempt = session.get(ProcessingAttempt, (row.invoice_id, row.attempts))
            attempt.finished_at = now
            attempt.reported_tokens = result.usage.total_tokens if result.usage else None
            row.extraction = encoded
            row.lease_token = None
            row.lease_until = None
            self._event(session, row, 'EXTRACTED', now)

    def failed(self, claim, code, *, retryable, retry_after=None):
        if code not in SAFE_CODES:
            code = 'processing_failed'
        with self._transaction() as session:
            self._lock(session)
            now = self._now()
            row = self._fence(session, claim, now, 'EXTRACTING')
            attempt = session.get(ProcessingAttempt, (row.invoice_id, row.attempts))
            attempt.finished_at = now
            attempt.error_code = code
            self._retry(session, row, now, code, retryable, retry_after)

    def claim_evaluation(self):
        with self._transaction() as session:
            self._lock(session)
            now = self._now()
            self._recover(session, now)
            count = session.scalar(select(func.count()).select_from(UploadCase).where(UploadCase.state == 'EVALUATING'))
            if count >= self.config.max_active:
                return None
            row = session.scalar(select(UploadCase).where(UploadCase.state == 'EXTRACTED').order_by(
                UploadCase.created_at, UploadCase.invoice_id).limit(1).with_for_update(skip_locked=True))
            return self._lease(session, row, now, 'EVALUATING') if row else None

    def complete(self, claim, callback):
        with self._transaction() as session:
            # Initial completion can now authorize payment. Join AP serialization
            # before reading balances; RC sees commits made while waiting for it.
            # No network is permitted in this transaction/callback.
            from app.application.use_cases.payment_authorization import automatically_approve
            from app.infrastructure.persistence.review_repository import SqlReviewTransaction, lock_payment_context

            session.connection(execution_options={'isolation_level': 'READ COMMITTED'})
            lock_payment_context(session)
            self._lock(session)
            now = self._now()
            row = self._fence(session, claim, now, 'EVALUATING')
            raw_id, evaluation_id, complete = callback(
                SqlAlchemyInvoiceEvaluationTransaction(session), self._snapshot(session, row))
            if type(complete) is not bool or (complete and (raw_id is None or evaluation_id is None)):
                raise ValueError('Complete processing requires linked evaluation evidence')
            if evaluation_id is not None:
                evaluation = SqlAlchemyInvoiceEvaluationTransaction(session).get_evaluation(evaluation_id)
                if evaluation is None or evaluation.invoice_id != raw_id or evaluation.result.complete != complete:
                    raise ValueError('Evaluation link does not match processing completion')
            # Fence again against clock expiry during a long callback. Any
            # staged evaluation writes roll back if the lease expired meanwhile.
            self._fence(session, claim, self._now(), 'EVALUATING')
            row.raw_invoice_id = raw_id
            row.evaluation_id = evaluation_id
            row.lease_token = None
            row.lease_until = None
            self._event(session, row, 'COMPLETED' if complete else 'EVALUATION_INCOMPLETE', self._now())
            if complete:
                review = SqlReviewTransaction(session)
                state = review.get_case(row.invoice_id)
                if automatically_approve(review, state, evaluation, now):
                    state.update(revision=state['revision'] + 1, updated_at=now)
                    review.store_case(state)
