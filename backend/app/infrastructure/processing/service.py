"""Authenticated document workflow over existing private storage and durable jobs."""
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from app.application.ports.auth import AuthError
from app.application.ports.invoice_documents import DocumentValidationError
from app.application.use_cases.process_invoice import BoundedOffload
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository


class InvoiceError(ValueError):
    def __init__(self, code, message, status=409, retryable=False):
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable


def public_value(value):
    """Exact money strings; no internal storage keys or provider response IDs."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


class InvoiceService:
    def __init__(self, sessions, repository, validator, storage, *, offload=None, estimated_tokens=6000):
        self.sessions = sessions
        self.repository = repository
        self.validator = validator
        self.storage = storage
        self.offload = offload or BoundedOffload()
        self.estimated_tokens = estimated_tokens

    async def authorize_upload(self, token, csrf):
        return await self.offload(self.sessions.require, token, csrf)

    async def upload(self, token, csrf, key, filename, media_type, content):
        # Authorization belongs here even if a router already checked it.
        actor = await self.authorize_upload(token, csrf)
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or not key.strip():
            raise InvoiceError('invalid_idempotency_key', 'Idempotency-Key is required (1-200 characters)', 422)
        document = await self.offload(self.validator.validate, filename, media_type, content)

        def store_and_admit():
            storage_key = self.storage.put(document)
            try:
                job = self.repository.admit(actor, key, storage_key, document.metadata, self.estimated_tokens)
            except Exception:
                self.storage.delete(storage_key)
                raise
            if job.storage_key != storage_key:
                self.storage.delete(storage_key)
                if (job.metadata.sha256 != document.metadata.sha256
                        or job.metadata.media_type != document.metadata.media_type):
                    raise InvoiceError('idempotency_conflict', 'Idempotency-Key belongs to a different document')
            return job
        # Cancellation cannot strand the newly stored artifact between calls.
        return self.snapshot(await self.offload(store_and_admit))

    async def _actor(self, token):
        return (await self.offload(self.sessions.resolve, token)).actor

    async def _job(self, actor, invoice_id):
        job = await self.offload(self.repository.get, actor, invoice_id)
        if job is None:
            raise InvoiceError('not_found', 'Invoice not found', 404)
        return job

    def snapshot(self, job):
        retryable = job.state in ('QUEUED', 'EXTRACTING', 'EXTRACTED', 'EVALUATING', 'RETRY_WAIT')
        data = public_value(dict(invoice_id=job.invoice_id, job_id=job.job_id,
                                 processing_status=job.state, attempts=job.attempts,
                                 next_eligible_at=job.next_eligible_at, created_at=job.created_at,
                                 updated_at=job.updated_at, automatic_outcome=None,
                                 error=(dict(code=job.error_code, message='Processing could not finish this attempt',
                                             retryable=retryable) if job.error_code else None)))
        if job.document_validation is not None:
            data['document_validation'] = public_value(job.document_validation)
            data['automatic_outcome'] = job.document_validation['automatic_outcome']
        return data

    async def list(self, token, *, status=None, offset=0, limit=20):
        actor = await self._actor(token)
        jobs = await self.offload(self.repository.list, actor)
        selected = [job for job in jobs if status is None or job.state == status]
        page = selected[offset:offset + limit]

        def project():
            items = []
            for job in page:
                data = self.snapshot(job)
                draft = job.extraction.invoice if job.extraction else None
                for field in ('invoice_number', 'supplier_name', 'invoice_date', 'total_amount', 'currency'):
                    data[field] = public_value(getattr(draft, field)) if draft is not None else None
                items.append(data)
            # Read only the authorized, selected page's persisted final outcomes.
            # Incomplete evaluations retain null; provisional decisions are not outcomes.
            if any(job.evaluation_id is not None for job in page):
                with SqlAlchemyInvoiceEvaluationRepository(self.repository.engine).transaction() as tx:
                    for job, data in zip(page, items):
                        if job.evaluation_id is not None:
                            record = tx.get_evaluation(job.evaluation_id)
                            if record is not None:
                                data['automatic_outcome'] = public_value(record.result.final_status)
            return items

        return dict(items=await self.offload(project), total=len(selected), offset=offset, limit=limit)

    async def detail(self, token, invoice_id):
        job = await self._job(await self._actor(token), invoice_id)
        data = self.snapshot(job)
        data['document'] = public_value(job.metadata)
        data['extraction'] = (public_value(dict(is_invoice=job.extraction.is_invoice,
                                               invoice=job.extraction.invoice,
                                               warnings=job.extraction.warnings)) if job.extraction else None)
        if job.extraction is not None and job.extraction.document_type is not None:
            data['extraction']['document_type'] = job.extraction.document_type
        data['evaluation'] = None
        data['findings'] = []
        if job.document_validation is not None:
            data['findings'] = public_value(job.document_validation['findings'])
        elif job.evaluation_id is not None:
            def read():
                with SqlAlchemyInvoiceEvaluationRepository(self.repository.engine).transaction() as tx:
                    return tx.get_evaluation(job.evaluation_id)
            record = await self.offload(read)
            if record is not None:
                data['evaluation'] = public_value(record)
                data['automatic_outcome'] = record.result.final_status
                data['findings'] = public_value(record.result.rules)
        elif job.state == 'EVALUATION_INCOMPLETE':
            data['findings'] = [dict(code='missing_evidence', outcome='PENDING',
                                     reason=('Required invoice facts are missing; review the extracted fields.'
                                             if job.extraction and job.extraction.document_type == 'invoice' else
                                             'Document is not an invoice or required invoice facts are missing'))]
        return data

    async def processing(self, token, invoice_id):
        job = await self._job(await self._actor(token), invoice_id)
        data = self.snapshot(job)
        data['attempt_history'] = public_value(job.attempt_history)
        data['events'] = public_value(job.events)
        return data

    async def retry(self, token, csrf, invoice_id):
        actor = await self.offload(self.sessions.require, token, csrf)
        job = await self._job(actor, invoice_id)
        if job.state not in ('QUEUED', 'EXTRACTING', 'EXTRACTED', 'EVALUATING', 'RETRY_WAIT'):
            raise InvoiceError('retry_not_allowed',
                               'No retry is available: processing is terminal or the finite attempt budget is exhausted')
        # Already scheduled: never advance eligibility, clear failures or reset attempts.
        return self.snapshot(job)

    async def document(self, token, invoice_id):
        job = await self._job(await self._actor(token), invoice_id)
        content = await self.offload(self.storage.read, job.storage_key)
        return content, job.metadata.media_type
