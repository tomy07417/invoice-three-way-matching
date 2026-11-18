"""Authenticated AP workflows; one transaction owns each versioned mutation."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.application.dtos.auth import Role
from app.application.dtos.invoice_documents import ExtractionResult
from app.application.dtos.review import ReviewError, parse_review_draft, review_value
from app.application.dtos.review_comparison import comparison_rows
from app.application.ports.auth import AuthError
from app.application.ports.review import ReviewRepository
from app.application.use_cases.process_invoice import evaluate_draft
from app.domain.matching.duplicates import normalize_invoice_number
from app.domain.matching.types import LineAssociation
from app.domain.review import PolicyError, approval_eligibility, authorize_decision, normalize_comment

_AP_ROLES = (Role.AP_ANALYST, Role.AP_MANAGER)


class ReviewService:
    def __init__(self, sessions, repository: ReviewRepository, *, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.repository, self.clock = sessions, repository, clock

    def _actor(self, token, csrf=None, *, write=False):
        if write:
            return self.sessions.require(token, csrf, roles=_AP_ROLES)
        actor = self.sessions.resolve(token).actor
        if actor.role not in _AP_ROLES:
            raise AuthError('AP Analyst or AP Manager access is required', 403)
        return actor

    @staticmethod
    def _comment(value):
        try:
            comment = normalize_comment(value)
        except PolicyError as error:
            raise ReviewError(error.code, str(error), 422) from None
        if len(comment) > 4000:
            raise ReviewError('invalid_comment', 'Comment must not exceed 4000 characters', 422)
        return comment

    @staticmethod
    def _limit(record):
        return record.result.config.analyst_approval_limit if record else Decimal('5000')

    def _view(self, tx, state, actor):
        record = tx.get_evaluation(state['evaluation_id'])
        draft = state['extraction'] or {}
        total = Decimal(draft['total_amount']) if draft.get('total_amount') is not None else None
        eligibility = approval_eligibility(actor.role.value, total, draft.get('currency'), self._limit(record))
        result = {key: state[key] for key in ('invoice_id', 'revision', 'extraction_version', 'extraction',
                  'original_extraction', 'operational_state', 'payment_blocked', 'credit_note_pending',
                  'requires_reevaluation', 'created_at', 'updated_at')}
        result.update(evaluation=record, comparison_rows=(comparison_rows(record.input_snapshot,
                      record.context_snapshot, record.result.rules) if record else []),
                      current_decision=tx.get_decision(state['current_decision_id']), eligibility=eligibility,
                      document_url=f"/invoices/{state['invoice_id']}/document")
        return review_value(result)

    def detail(self, token, invoice_id):
        actor = self._actor(token)
        with self.repository.transaction() as tx:
            return self._view(tx, tx.get_case(invoice_id), actor)

    def history(self, token, invoice_id):
        self._actor(token)
        with self.repository.transaction() as tx:
            state = tx.get_case(invoice_id)
            return review_value(dict(invoice_id=invoice_id, revision=state['revision'], events=tx.history(state)))

    def queue(self, token, **filters):
        actor = self._actor(token)
        now = self.clock()
        with self.repository.transaction() as tx:
            states, total = tx.queue(now=now, **filters)
            items = []
            for state in states:
                view = self._view(tx, state, actor)
                draft = state['extraction'] or {}
                record = tx.get_evaluation(state['evaluation_id'])
                items.append(dict(invoice_id=str(state['invoice_id']), revision=state['revision'],
                    **{key: draft.get(key) for key in ('invoice_number', 'supplier_name', 'invoice_date', 'total_amount', 'currency')},
                    operational_state=state['operational_state'], payment_blocked=state['payment_blocked'],
                    automatic_outcome=record.result.final_status if record else None,
                    reasons=[rule.reason for rule in record.result.rules if rule.outcome != 'PASS'] if record else [],
                    requires_manager=view['eligibility']['requires_manager'], created_at=state['created_at'],
                    age_seconds=max(0, int((now - state['created_at']).total_seconds()))))
        return review_value(dict(items=items, total=total, offset=filters.get('offset', 0), limit=filters.get('limit', 20)))

    @staticmethod
    def _locked(tx, invoice_id, expected):
        state = tx.get_case(invoice_id, lock=True)
        if type(expected) is not int or expected < 0:
            raise ReviewError('invalid_revision', 'A valid expected case revision is required', 422)
        if state['revision'] != expected:
            raise ReviewError('stale_revision', 'The case changed; refresh and review the current version')
        tx.ensure_case(state)
        return state

    def _finish(self, tx, state, actor, kind, comment, payload, at):
        state['revision'] += 1
        state['updated_at'] = at
        tx.store_case(state)
        tx.audit(state, actor, kind, comment, payload, at)
        return self._view(tx, state, actor)

    @staticmethod
    def _invalidate(tx, state, actor, comment, at):
        old = state['current_decision_id']
        if tx.reverse_approval(old, actor, comment, at):
            audit_state = {**state, 'revision': state['revision'] + 1}
            tx.audit(audit_state, actor, 'approval_reversal', comment, {'decision_id': old}, at)
        state['current_decision_id'] = None
        state['payment_blocked'] = True

    def correct(self, token, csrf, invoice_id, expected, invoice, comment):
        actor = self._actor(token, csrf, write=True)
        comment = self._comment(comment)
        draft = parse_review_draft(invoice)
        at = self.clock()
        with self.repository.transaction(write=True) as tx:
            state = self._locked(tx, invoice_id, expected)
            if review_value(draft) == state['extraction']:
                raise ReviewError('unchanged_correction', 'The correction must change invoice data', 422)
            self._invalidate(tx, state, actor, comment, at)
            state.update(extraction=review_value(draft), extraction_version=state['extraction_version'] + 1,
                         evaluation_id=None, raw_invoice_id=None, operational_state='REEVALUATION_REQUIRED',
                         requires_reevaluation=True)
            tx.append_version(state, actor, comment, at)
            return self._finish(tx, state, actor, 'extraction', comment,
                {'extraction_version': state['extraction_version'], 'invoice': state['extraction']}, at)

    def reevaluate(self, token, csrf, invoice_id, expected, comment):
        actor = self._actor(token, csrf, write=True)
        comment = self._comment(comment)
        at = self.clock()
        with self.repository.transaction(write=True) as tx:
            state = self._locked(tx, invoice_id, expected)
            self._invalidate(tx, state, actor, comment, at)
            draft = parse_review_draft(state['extraction']) if state['extraction'] is not None else None
            job = SimpleNamespace(owner_id=actor.actor_id, extraction=ExtractionResult(True, draft, ()))
            raw_id, evaluation_id, complete = evaluate_draft(tx.evaluation_transaction(state), job,
                                                           lambda: at, origin='ap-review-reevaluation')
            state.update(raw_invoice_id=raw_id, evaluation_id=evaluation_id, requires_reevaluation=False,
                         operational_state='CREDIT_NOTE_REQUESTED' if state['credit_note_pending'] else 'REVIEW_REQUIRED')
            if evaluation_id is not None:
                tx.link_evaluation(state)
            record = tx.get_evaluation(evaluation_id)
            return self._finish(tx, state, actor, 'evaluation', comment,
                {'extraction_version': state['extraction_version'], 'evaluation_id': evaluation_id,
                 'evaluation': record, 'complete': complete}, at)

    @staticmethod
    def _approval_quantities(tx, state, record):
        """Fresh physical availability and payment identity; overrides cannot bypass them."""
        if state['credit_note_pending']:
            raise ReviewError('credit_note_pending', 'A pending credit-note request blocks payment authorization')
        if record is None or not record.result.complete or state['raw_invoice_id'] != record.invoice_id:
            raise ReviewError('incomplete_evidence', 'Complete, current evaluation evidence is required before approval')
        draft = parse_review_draft(state['extraction'])
        evaluation = tx.evaluation_transaction(state)
        reference = evaluation.load_reference_data()
        suppliers = [supplier for supplier in reference.suppliers if supplier.tax_id == draft.supplier_tax_id]
        number = normalize_invoice_number(draft.invoice_number or '')
        if len(suppliers) != 1 or not number:
            raise ReviewError('incomplete_evidence', 'Resolve supplier identity and invoice number before approval')
        if any(supplier == suppliers[0].supplier_id and normalize_invoice_number(previous) == number
               for supplier, previous in tx.active_approved_invoices()):
            raise ReviewError('already_authorized', 'This supplier and invoice number already have an active payment authorization')
        purchase_orders = {po.po_number: po for po in reference.purchase_orders}
        grouped = defaultdict(Decimal)
        for line in draft.lines:
            po = purchase_orders.get(line.po_number)
            if (po is None or line.po_number not in (draft.po_references or ())
                    or not any(item.line_number == line.po_line_number for item in po.lines)
                    or line.quantity is None or not line.quantity.is_finite() or line.quantity <= 0
                    or line.unit_price_basis != 'net' or line.unit_price is None or line.unit_price <= 0):
                raise ReviewError('unresolved_line', 'Every line needs a reliable PO link and positive quantity/net price before approval')
            grouped[LineAssociation(line.po_number, line.po_line_number)] += line.quantity
        if not grouped:
            raise ReviewError('incomplete_evidence', 'At least one fully associated invoice line is required')
        approved = evaluation.get_approved_quantities(tuple(purchase_orders))
        received = defaultdict(Decimal)
        for note in reference.goods_received_notes:
            for line in note.lines:
                received[LineAssociation(note.po_number, line.po_line_number)] += line.quantity_received
        proof = []
        for link, quantity in grouped.items():
            consumed = approved.get(link, Decimal(0))
            available = received[link] - consumed
            if quantity > available:
                raise ReviewError('insufficient_available_quantity',
                                  'Received quantity is no longer available; refresh evidence before approving')
            proof.append(dict(po_number=link.po_number, po_line_number=link.po_line_number,
                              quantity=quantity, received=received[link], previously_approved=consumed, available=available))
        return grouped, proof

    def decide(self, token, csrf, invoice_id, expected, action, comment):
        actor = self._actor(token, csrf, write=True)
        comment = self._comment(comment)
        at = self.clock()
        with self.repository.transaction(write=True) as tx:
            state = self._locked(tx, invoice_id, expected)
            if state['requires_reevaluation']:
                raise ReviewError('reevaluation_required', 'Reevaluate the corrected extraction before making a decision')
            if state['current_decision_id'] is not None:
                raise ReviewError('decision_exists', 'This version already has a decision; correct or reevaluate before another decision')
            record = tx.get_evaluation(state['evaluation_id'])
            draft = state['extraction'] or {}
            total = Decimal(draft['total_amount']) if draft.get('total_amount') is not None else None
            try:
                authorize_decision(actor.role.value, action, total, draft.get('currency'), comment, self._limit(record))
            except PolicyError as error:
                status = 403 if error.code in ('forbidden_role', 'approval_blocked') else 422
                raise ReviewError(error.code, str(error), status) from None
            quantities, proof = self._approval_quantities(tx, state, record) if action == 'approve' else ({}, [])
            state['current_decision_id'] = tx.append_decision(state, action, actor, comment, at)
            state['operational_state'] = {'approve': 'APPROVED', 'reject': 'REJECTED',
                                           'request_credit_note': 'CREDIT_NOTE_REQUESTED'}[action]
            state['payment_blocked'] = action != 'approve'
            if action == 'request_credit_note':
                state['credit_note_pending'] = True
            if quantities:
                tx.append_consumption(state, quantities, actor, at)
            return self._finish(tx, state, actor, 'decision', comment,
                {'action': action, 'decision_id': state['current_decision_id'],
                 'extraction_version': state['extraction_version'], 'evaluation_id': state['evaluation_id'],
                 'approval_time_quantities': proof}, at)

    def escalate(self, token, csrf, invoice_id, expected, comment):
        actor = self._actor(token, csrf, write=True)
        comment = self._comment(comment)
        at = self.clock()
        with self.repository.transaction(write=True) as tx:
            state = self._locked(tx, invoice_id, expected)
            record = tx.get_evaluation(state['evaluation_id'])
            draft = state['extraction'] or {}
            total = Decimal(draft['total_amount']) if draft.get('total_amount') is not None else None
            eligibility = approval_eligibility(Role.AP_ANALYST.value, total, draft.get('currency'), self._limit(record))
            if state['requires_reevaluation'] or state['current_decision_id'] is not None or not eligibility['requires_manager']:
                raise ReviewError('escalation_not_required', 'This current undecided case does not require amount/currency escalation')
            state['operational_state'] = 'ESCALATED'
            return self._finish(tx, state, actor, 'escalation', comment, {'reason': eligibility['reason']}, at)
