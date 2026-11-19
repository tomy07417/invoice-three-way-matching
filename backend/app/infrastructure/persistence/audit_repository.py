"""Truthful projection of existing immutable facts in a read-only RR snapshot.

Review's original version metadata is synthesized from upload ownership/time.
It is deliberately NOT used as extraction attribution. No ensure_case/history
helper is called: reads must neither create review state nor inherit that fiction.
"""
from copy import deepcopy
from dataclasses import fields

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.dtos.audit import (
    AuditActor, AuditDiff, AuditError, AuditEvent, AuditProvenance, AuditSnapshot,
)
from app.application.dtos.automation import AUTO_APPROVER_ID
from app.application.dtos.invoice_documents import InvoiceDraft, InvoiceLineDraft
from app.application.dtos.review import review_value
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationTransaction
from app.infrastructure.persistence.processing_models import ProcessingEvent, UploadCase
from app.infrastructure.persistence.review_models import (
    ReviewAuditEvent, ReviewCase, ReviewDecision, ReviewEvaluationLink, ReviewExtractionVersion,
)


def _draft(value):
    """Explicit invoice fields only, not provider envelopes or ORM dictionaries."""
    if not isinstance(value, dict):
        return None
    result = {item.name: deepcopy(value.get(item.name)) for item in fields(InvoiceDraft) if item.name != 'lines'}
    if value.get('accounting_evidence') is None:
        result.pop('accounting_evidence', None)
    result['lines'] = [{item.name: deepcopy(line.get(item.name)) for item in fields(InvoiceLineDraft)}
                       for line in value.get('lines', [])]
    return result


def _diff(before, after, source, path=''):
    """Compare only authoritative stored versions; preserve exact decimal text."""
    if before == after:
        return ()
    if isinstance(before, dict) and isinstance(after, dict) and before.keys() == after.keys():
        return tuple(change for key in sorted(before) for change in
                     _diff(before[key], after[key], source, f'{path}.{key}' if path else key))
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return tuple(change for index, (old, new) in enumerate(zip(before, after))
                     for change in _diff(old, new, source, f'{path}.{index}'))
    return (AuditDiff(path, before, after, True, True, source),)


def _recorded(actor_id, name=None, role=None):
    if actor_id == AUTO_APPROVER_ID:
        return AuditActor(actor_id, name, None, 'system')
    return AuditActor(str(actor_id), name, role, 'recorded') if actor_id is not None else AuditActor()


def _evaluation(record):
    """EvaluationRecord is the existing detached, secret-free evaluation DTO."""
    return review_value(dict(evaluation_id=record.evaluation_id, invoice_id=record.invoice_id,
        configuration_id=record.configuration_id, evaluated_at=record.evaluated_at,
        actor_id=record.actor_id, origin=record.origin, result=record.result,
        input_snapshot=record.input_snapshot, context_snapshot=record.context_snapshot))


class SqlAuditRepository:
    def __init__(self, engine):
        self.engine = engine

    def read(self, invoice_id):
        try:
            with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
                with Session(connection) as session, session.begin():
                    session.execute(text('SET TRANSACTION READ ONLY'))
                    session.execute(text("SET LOCAL statement_timeout = '15s'"))
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    return self._read(session, invoice_id)
        except SQLAlchemyError:
            raise AuditError('audit_unavailable', 'Audit service is temporarily unavailable', 503) from None

    @staticmethod
    def _read(session, invoice_id):
        upload = session.get(UploadCase, invoice_id)
        if upload is None:
            raise AuditError('not_found', 'Invoice not found', 404)
        state = session.get(ReviewCase, invoice_id)
        revision = state.revision if state else 0
        current_version = state.extraction_version if state else 1
        current_evaluation = state.evaluation_id if state else upload.evaluation_id
        current_decision = state.current_decision_id if state else None
        evaluations_applicable = not state or state.operational_state != 'REEVALUATION_REQUIRED'
        processing = session.scalars(select(ProcessingEvent).where(ProcessingEvent.invoice_id == invoice_id)
                                     .order_by(ProcessingEvent.id)).all()
        versions = session.scalars(select(ReviewExtractionVersion).where(
            ReviewExtractionVersion.invoice_id == invoice_id).order_by(ReviewExtractionVersion.number)).all()
        audit_rows = session.scalars(select(ReviewAuditEvent).where(
            ReviewAuditEvent.invoice_id == invoice_id).order_by(ReviewAuditEvent.revision, ReviewAuditEvent.id)).all()
        decisions = session.scalars(select(ReviewDecision).where(ReviewDecision.invoice_id == invoice_id)
                                   .order_by(ReviewDecision.revision, ReviewDecision.id)).all()
        links = session.scalars(select(ReviewEvaluationLink).where(ReviewEvaluationLink.invoice_id == invoice_id)).all()
        extraction = _draft((upload.extraction or {}).get('invoice'))
        drafts = {version.number: _draft(version.draft) for version in versions}
        # The immutable upload is the actual original extraction, not version1's
        # fabricated actor/time. Versions supply authoritative corrected contents.
        drafts[1] = extraction
        events = [AuditEvent('upload:creation', 'creation', upload.created_at, _recorded(upload.owner_id),
                            AuditProvenance('upload_cases', str(invoice_id)),
                            evidence={'processing_status': 'QUEUED'})]
        previous_state = None
        for row in processing:
            # Worker-only state transitions establish automation. QUEUED can also
            # be a user retry request and ProcessingEvent records no requester.
            actor = AuditActor(type='system') if row.state != 'QUEUED' else AuditActor()
            events.append(AuditEvent(f'processing:{row.id}:state', 'state_change', row.at, actor,
                AuditProvenance('processing_events', str(row.id)),
                diffs=(AuditDiff('processing_status', previous_state, row.state, previous_state is not None,
                                True, 'processing_events'),), evidence={'processing_status': row.state}))
            previous_state = row.state
        extracted = [row for row in processing if row.state == 'EXTRACTED']
        if upload.extraction is not None:
            source = extracted[-1] if extracted else None
            events.append(AuditEvent('upload:extraction', 'extraction', source.at if source else None,
                AuditActor(type='system') if source else AuditActor(),
                AuditProvenance('processing_events' if source else 'upload_cases.extraction',
                    str(source.id) if source else str(invoice_id), note=(
                    'Worker persisted extraction; actor identity is not recorded' if source else
                    'Historical extraction time and actor are unknown; upload metadata is not extraction metadata')),
                extraction_version=1, current=current_version == 1, evidence={'invoice': extraction}))
        evaluation_versions = {link.evaluation_id: link.extraction_version for link in links}
        if upload.evaluation_id is not None:
            evaluation_versions[upload.evaluation_id] = 1
        evaluation_tx = SqlAlchemyInvoiceEvaluationTransaction(session)
        previous_evaluation = None
        for evaluation_id, version in sorted(evaluation_versions.items()):
            record = evaluation_tx.get_evaluation(evaluation_id)
            if record is None:
                continue
            related = next((row for row in audit_rows if row.kind == 'evaluation'
                            and row.payload.get('evaluation_id') == evaluation_id), None)
            original = evaluation_id == upload.evaluation_id
            events.append(AuditEvent(f'evaluation:{evaluation_id}', 'evaluation' if original else 'reevaluation',
                record.evaluated_at, _recorded(record.actor_id),
                AuditProvenance('invoice_evaluations', str(evaluation_id), note=(
                    'Recorded initiating actor and origin, not inferred automated actor; immutable evaluation snapshots')),
                revision=related.revision if related else None, extraction_version=version,
                evaluation_id=evaluation_id, current=current_evaluation == evaluation_id and evaluations_applicable,
                comment=related.comment if related else None,
                diffs=(_diff(previous_evaluation, review_value(record.result), 'invoice_evaluations', 'result')
                       if previous_evaluation is not None else ()),
                evidence={'evaluation': _evaluation(record)}))
            previous_evaluation = review_value(record.result)
        for version in versions:
            if version.number == 1:
                continue
            related = next((row for row in audit_rows if row.kind == 'extraction'
                            and row.payload.get('extraction_version') == version.number), None)
            actor = (_recorded(related.actor_id, related.actor_name, related.actor_role) if related else
                     _recorded(version.actor_id))
            before_known = version.number - 1 in drafts and drafts[version.number - 1] is not None
            diffs = (_diff(drafts[version.number - 1], drafts[version.number], 'review_extraction_versions')
                     if before_known else (AuditDiff('invoice', None, drafts[version.number], False, True,
                                                   'review_extraction_versions'),))
            events.append(AuditEvent(f'version:{version.number}', 'correction', version.at, actor,
                AuditProvenance('review_extraction_versions', f'{invoice_id}:{version.number}'),
                revision=related.revision if related else None, extraction_version=version.number,
                current=current_version == version.number, comment=version.comment,
                diffs=diffs, evidence={'invoice': drafts[version.number]}))
        for decision in decisions:
            related = next((row for row in audit_rows if row.kind == 'decision'
                            and row.payload.get('decision_id') == decision.id), None)
            actor = (_recorded(related.actor_id, related.actor_name, related.actor_role) if related else
                     _recorded(decision.actor_id))
            values = dict(at=decision.at, actor=actor,
                provenance=AuditProvenance('review_decisions', str(decision.id)),
                revision=decision.revision, extraction_version=decision.extraction_version,
                evaluation_id=decision.evaluation_id, current=current_decision == decision.id,
                comment=decision.comment, evidence={'decision_id': decision.id, 'action': decision.action})
            events.append(AuditEvent(f'decision:{decision.id}', 'decision', **values))
            if decision.action == 'request_credit_note':
                events.append(AuditEvent(f'decision:{decision.id}:credit', 'credit_note_request', **values))
        for row in audit_rows:
            actor = _recorded(row.actor_id, row.actor_name, row.actor_role)
            provenance = AuditProvenance('review_audit_events', str(row.id))
            if row.kind == 'escalation':
                events.append(AuditEvent(f'audit:{row.id}', 'escalation', row.at, actor, provenance,
                    revision=row.revision, comment=row.comment, evidence={'reason': row.payload.get('reason')}))
            elif row.kind == 'approval_reversal':
                events.append(AuditEvent(f'audit:{row.id}', 'approval_reversal', row.at, actor, provenance,
                    revision=row.revision, comment=row.comment, evidence={'decision_id': row.payload.get('decision_id')}))
            elif row.kind == 'evaluation' and row.payload.get('evaluation_id') is None:
                events.append(AuditEvent(f'audit:{row.id}', 'reevaluation', row.at, actor, provenance,
                    revision=row.revision, extraction_version=row.payload.get('extraction_version'),
                    comment=row.comment, evidence={'evaluation': None}))
            # Stored review events do not carry before-state. Do not replay
            # today's policy to invent it. Only project after-values established
            # by the recorded action's semantics and mark this derivation.
            after = None
            if row.kind == 'extraction':
                after = 'REEVALUATION_REQUIRED'
            elif row.kind == 'escalation':
                after = 'ESCALATED'
            elif row.kind == 'decision':
                after = {'approve': 'APPROVED', 'reject': 'REJECTED',
                         'request_credit_note': 'CREDIT_NOTE_REQUESTED'}.get(row.payload.get('action'))
            if after is not None:
                events.append(AuditEvent(f'audit:{row.id}:state', 'state_change', row.at, actor,
                    AuditProvenance('review_audit_events', str(row.id), True,
                        'After-state derived from recorded action semantics; before-state was not recorded'),
                    revision=row.revision, diffs=(AuditDiff('operational_state', None, after, False, True,
                                                           'review_audit_events'),)))
        return AuditSnapshot(invoice_id, revision, tuple(events))
