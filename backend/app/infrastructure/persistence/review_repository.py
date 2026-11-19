"""Short review transactions; no HTTP, model calls or payment execution.

A single advisory lock serializes review mutations in this local-demo monolith.
READ COMMITTED obtains fresh consumption after waiting, unlike a stale snapshot
acquired before the lock. Shared reference-table locks keep imports from changing
the comparison context during an approval. This favors correctness over scale.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import Numeric, case, exists, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.dtos.review import ReviewError, review_value
from app.infrastructure.persistence import models
from app.infrastructure.persistence.evaluation_records import ApprovedPaymentQuantity, EvaluationRuleFinding, InvoiceEvaluation
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationTransaction
from app.infrastructure.persistence.processing_models import UploadCase
from app.infrastructure.persistence.review_models import (
    ReviewApprovalLink, ReviewApprovalReversal, ReviewAuditEvent, ReviewCase,
    ReviewDecision, ReviewEvaluationLink, ReviewExtractionVersion,
)
from app.infrastructure.persistence.review_visibility import active_approval_clause


def lock_payment_context(session):
    """Serialize balances before their RC snapshot; keep reference imports stable."""
    session.execute(text('SELECT pg_advisory_xact_lock(78390401)'))
    session.execute(text('LOCK TABLE suppliers, purchase_orders, purchase_order_lines, '
                         'goods_received_notes, goods_received_note_lines IN SHARE MODE'))


class ReviewEvaluationTransaction(SqlAlchemyInvoiceEvaluationTransaction):
    def __init__(self, session, version):
        super().__init__(session)
        self.version = version

    def register_invoice(self, invoice, supplier_id, invoice_id=None):
        raw_id = super().register_invoice(invoice, supplier_id, self.version.raw_invoice_id)
        self.version.raw_invoice_id = raw_id
        self.session.flush()  # Same-case history exclusion must see this version.
        return raw_id


class SqlReviewTransaction:
    def __init__(self, session):
        self.session = session
        self.evaluations = SqlAlchemyInvoiceEvaluationTransaction(session)

    def get_case(self, invoice_id, *, lock=False):
        query = select(UploadCase).where(UploadCase.invoice_id == invoice_id)
        upload = self.session.scalar(query.with_for_update() if lock else query)
        if upload is None:
            raise ReviewError('not_found', 'Invoice not found', 404)
        if upload.state not in ('COMPLETED', 'EVALUATION_INCOMPLETE'):
            raise ReviewError('not_ready', 'Wait for extraction and evaluation to finish')
        state = self.session.get(ReviewCase, invoice_id)
        version = (self.session.get(ReviewExtractionVersion, (invoice_id, state.extraction_version))
                   if state is not None else None)
        original = (upload.extraction or {}).get('invoice')
        return dict(invoice_id=invoice_id, revision=state.revision if state else 0,
                    extraction_version=state.extraction_version if state else 1,
                    evaluation_id=state.evaluation_id if state else upload.evaluation_id,
                    current_decision_id=state.current_decision_id if state else None,
                    operational_state=state.operational_state if state else 'REVIEW_REQUIRED',
                    payment_blocked=state.payment_blocked if state else True,
                    credit_note_pending=state.credit_note_pending if state else False,
                    updated_at=state.updated_at if state else upload.updated_at,
                    created_at=upload.created_at, owner_id=upload.owner_id,
                    extraction=deepcopy(version.draft if version else original),
                    original_extraction=deepcopy(original),
                    raw_invoice_id=version.raw_invoice_id if version else upload.raw_invoice_id,
                    requires_reevaluation=state is not None and state.operational_state == 'REEVALUATION_REQUIRED')

    def ensure_case(self, state):
        if self.session.get(ReviewCase, state['invoice_id']) is not None:
            return
        self.store_case(state, new=True)
        self.session.add(ReviewExtractionVersion(
            invoice_id=state['invoice_id'], number=1, draft=deepcopy(state['extraction']),
            raw_invoice_id=state['raw_invoice_id'], actor_id=str(state['owner_id']),
            at=state['created_at'], comment='Original extracted invoice'))
        if state['evaluation_id'] is not None:
            self.session.add(ReviewEvaluationLink(evaluation_id=state['evaluation_id'],
                                                  invoice_id=state['invoice_id'], extraction_version=1))
        self.session.flush()

    def store_case(self, state, *, new=False):
        values = {key: state[key] for key in ('revision', 'extraction_version', 'evaluation_id',
                  'current_decision_id', 'operational_state', 'payment_blocked', 'credit_note_pending', 'updated_at')}
        if new:
            self.session.add(ReviewCase(invoice_id=state['invoice_id'], **values))
        else:
            row = self.session.get(ReviewCase, state['invoice_id'])
            for key, value in values.items():
                setattr(row, key, value)
        self.session.flush()

    def append_version(self, state, actor, comment, at):
        self.session.add(ReviewExtractionVersion(
            invoice_id=state['invoice_id'], number=state['extraction_version'],
            draft=deepcopy(state['extraction']), raw_invoice_id=None,
            actor_id=str(actor.actor_id), at=at, comment=comment))
        self.session.flush()

    def evaluation_transaction(self, state):
        version = self.session.get(ReviewExtractionVersion, (state['invoice_id'], state['extraction_version']))
        return ReviewEvaluationTransaction(self.session, version)

    def link_evaluation(self, state):
        self.session.add(ReviewEvaluationLink(evaluation_id=state['evaluation_id'],
                                              invoice_id=state['invoice_id'],
                                              extraction_version=state['extraction_version']))
        self.session.flush()

    def get_evaluation(self, evaluation_id):
        return self.evaluations.get_evaluation(evaluation_id) if evaluation_id is not None else None

    def get_decision(self, decision_id):
        row = self.session.get(ReviewDecision, decision_id) if decision_id is not None else None
        if row is None:
            return None
        return {key: getattr(row, key) for key in ('id', 'revision', 'extraction_version', 'evaluation_id',
                                                  'action', 'actor_id', 'at', 'comment')}

    def append_decision(self, state, action, actor, comment, at):
        row = ReviewDecision(invoice_id=state['invoice_id'], revision=state['revision'] + 1,
                             extraction_version=state['extraction_version'], evaluation_id=state['evaluation_id'],
                             action=action, actor_id=str(actor.actor_id), at=at, comment=comment)
        self.session.add(row)
        self.session.flush()
        return row.id

    def reverse_approval(self, decision_id, actor, comment, at):
        row = self.session.get(ReviewDecision, decision_id) if decision_id is not None else None
        if row is None or row.action != 'approve' or self.session.get(ReviewApprovalReversal, row.id) is not None:
            return False
        self.session.add(ReviewApprovalReversal(decision_id=row.id, actor_id=str(actor.actor_id), at=at, comment=comment))
        self.session.flush()
        return True

    def append_consumption(self, state, quantities, actor, at):
        for association, quantity in sorted(quantities.items(), key=lambda item: (item[0].po_number, item[0].po_line_number)):
            row = ApprovedPaymentQuantity(invoice_id=state['raw_invoice_id'], po_number=association.po_number,
                                          po_line_number=association.po_line_number, quantity=quantity,
                                          approved_at=at, actor_id=str(actor.actor_id))
            self.session.add(row)
            self.session.flush()
            self.session.add(ReviewApprovalLink(ledger_id=row.id, decision_id=state['current_decision_id']))
        self.session.flush()

    def active_approved_invoices(self):
        return self.session.execute(select(models.Invoice.resolved_supplier_id, models.Invoice.invoice_number)
            .join(ApprovedPaymentQuantity, ApprovedPaymentQuantity.invoice_id == models.Invoice.id)
            .where(active_approval_clause(ApprovedPaymentQuantity)).distinct()).all()

    def audit(self, state, actor, kind, comment, payload, at):
        self.session.add(ReviewAuditEvent(invoice_id=state['invoice_id'], revision=state['revision'],
            kind=kind, actor_id=str(actor.actor_id), actor_name=actor.username, actor_role=actor.role.value,
            at=at, comment=comment, payload=review_value(payload)))
        self.session.flush()

    def history(self, state):
        upload = self.session.get(UploadCase, state['invoice_id'])
        events = [dict(kind='extraction', at=upload.created_at, actor_id=str(upload.owner_id),
                       comment='Original extracted invoice', extraction_version=1,
                       current=state['extraction_version'] == 1, invoice=deepcopy(state['original_extraction']))]
        original = self.get_evaluation(upload.evaluation_id)
        if original is not None:
            events.append(dict(kind='evaluation', at=original.evaluated_at, actor_id=original.actor_id,
                comment='Original automatic evaluation', extraction_version=1, evaluation_id=original.evaluation_id,
                current=state['evaluation_id'] == original.evaluation_id and not state['requires_reevaluation'],
                evaluation=review_value(original)))
        rows = self.session.scalars(select(ReviewAuditEvent).where(
            ReviewAuditEvent.invoice_id == state['invoice_id']).order_by(ReviewAuditEvent.revision, ReviewAuditEvent.id))
        for row in rows:
            event = dict(kind=row.kind, at=row.at, actor_id=row.actor_id, actor_name=row.actor_name,
                         actor_role=row.actor_role, comment=row.comment, revision=row.revision, **deepcopy(row.payload))
            if row.kind == 'decision':
                event['current'] = event.get('decision_id') == state['current_decision_id']
            elif row.kind == 'evaluation':
                event['current'] = event.get('evaluation_id') == state['evaluation_id'] and not state['requires_reevaluation']
            elif row.kind == 'extraction':
                event['current'] = event.get('extraction_version') == state['extraction_version']
            else:
                event['current'] = False
            events.append(event)
        return events

    def queue(self, *, now, offset=0, limit=20, supplier=None, date_from=None, date_to=None,
              min_amount=None, max_amount=None, reason=None, operational_state=None,
              min_age_days=None, sort='created_at', direction='asc', outcome='Held for Review'):
        version = ReviewExtractionVersion
        draft = case((ReviewCase.invoice_id.is_not(None), version.draft), else_=UploadCase.extraction['invoice'])
        effective_evaluation = case((ReviewCase.invoice_id.is_not(None), ReviewCase.evaluation_id), else_=UploadCase.evaluation_id)
        state = func.coalesce(ReviewCase.operational_state, 'REVIEW_REQUIRED')
        query = select(UploadCase.invoice_id).outerjoin(ReviewCase, ReviewCase.invoice_id == UploadCase.invoice_id)
        query = query.outerjoin(version, (version.invoice_id == ReviewCase.invoice_id) & (version.number == ReviewCase.extraction_version))
        query = query.outerjoin(InvoiceEvaluation, InvoiceEvaluation.id == effective_evaluation)
        query = query.where(UploadCase.state.in_(('COMPLETED', 'EVALUATION_INCOMPLETE')))
        if outcome != 'all':
            query = query.where(InvoiceEvaluation.final_status == outcome)
        amount = draft['total_amount'].as_string().cast(Numeric)
        supplier_field = draft['supplier_name'].as_string()
        invoice_date = draft['invoice_date'].as_string()
        if supplier:
            query = query.where(supplier_field.ilike('%' + supplier + '%'))
        if date_from:
            query = query.where(invoice_date >= str(date_from))
        if date_to:
            query = query.where(invoice_date <= str(date_to))
        if min_amount is not None:
            query = query.where(amount >= min_amount)
        if max_amount is not None:
            query = query.where(amount <= max_amount)
        if operational_state:
            query = query.where(state == operational_state)
        if min_age_days is not None:
            query = query.where(UploadCase.created_at <= now - timedelta(days=min_age_days))
        if reason:
            query = query.where(exists(select(EvaluationRuleFinding.evaluation_id).where(
                EvaluationRuleFinding.evaluation_id == effective_evaluation,
                EvaluationRuleFinding.outcome != 'PASS', EvaluationRuleFinding.reason.ilike('%' + reason + '%'))))
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        order = {'created_at': UploadCase.created_at, 'age': UploadCase.created_at,
                 'amount': amount, 'supplier': supplier_field, 'invoice_date': invoice_date}[sort]
        if (direction == 'desc') != (sort == 'age'):
            order = order.desc()
        else:
            order = order.asc()
        ids = self.session.scalars(query.order_by(order.nulls_last(), UploadCase.invoice_id).offset(offset).limit(limit)).all()
        return [self.get_case(invoice_id) for invoice_id in ids], total


class SqlReviewRepository:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, *, write=False):
        try:
            with self.engine.connect().execution_options(isolation_level='READ COMMITTED') as connection:
                with Session(connection) as session, session.begin():
                    session.execute(text("SET LOCAL statement_timeout = '15s'"))
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    if write:
                        lock_payment_context(session)
                    yield SqlReviewTransaction(session)
        except SQLAlchemyError:
            raise ReviewError('review_unavailable', 'Review service is temporarily unavailable; refresh before retrying', 503) from None
