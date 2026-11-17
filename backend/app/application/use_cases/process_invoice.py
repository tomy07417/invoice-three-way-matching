"""Single durable processing tick; no network inside evaluation transactions."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import math

from app.application.dtos.invoice_documents import ValidatedDocument
from app.application.dtos.invoice_evaluation import EvaluateInvoiceCommand
from app.application.ports.invoice_documents import ExtractionError
from app.application.ports.processing import StaleLease
from app.application.use_cases.evaluate_invoice import EvaluateInvoiceUseCase
from app.domain.entities import Invoice, InvoiceLine
from app.domain.matching.types import LineAssociation


class BoundedOffload:
    """Bound submissions as well as running threads, including cancellation."""
    def __init__(self, limit=4):
        self.semaphore = asyncio.Semaphore(limit)

    async def __call__(self, operation, *args, **kwargs):
        async with self.semaphore:
            task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Do not release the capacity while the synchronous call lives.
                try:
                    await task
                finally:
                    raise


class _BorrowedTransaction:
    def __init__(self, transaction):
        self.value = transaction

    @contextmanager
    def transaction(self):
        # The enclosing fenced repository completion alone owns commit/rollback.
        yield self.value


def evaluate_draft(transaction, job, clock):
    result = job.extraction
    draft = result.invoice if result and result.is_invoice else None
    if draft is None:
        return None, None, False
    required = ('supplier_name', 'supplier_tax_id', 'invoice_number', 'invoice_date',
                'po_references', 'currency', 'subtotal', 'tax_amount', 'total_amount')
    if any(getattr(draft, key) is None for key in required) or not draft.lines:
        return None, None, False
    if any(not getattr(draft, key).strip() for key in
           ('supplier_name', 'supplier_tax_id', 'invoice_number', 'currency')):
        return None, None, False
    if any(any(getattr(line, key) is None for key in
               ('description', 'quantity', 'unit_price', 'line_total')) for line in draft.lines):
        return None, None, False
    values = asdict(draft)
    values['lines'] = tuple(InvoiceLine(line.description, line.quantity, line.unit_price,
                                       line.line_total, line.po_line_reference) for line in draft.lines)
    invoice = Invoice(**values)
    # Identity is resolved only against an exact, unique authoritative tax ID.
    reference = transaction.load_reference_data()
    suppliers = [supplier for supplier in reference.suppliers if supplier.tax_id == draft.supplier_tax_id]
    supplier_id = suppliers[0].supplier_id if len(suppliers) == 1 else None
    associations = {}
    net_prices = {}
    for index, line in enumerate(draft.lines):
        if (line.po_number in draft.po_references and line.po_line_number is not None
                and any(po.po_number == line.po_number and
                        any(item.line_number == line.po_line_number for item in po.lines)
                        for po in reference.purchase_orders)):
            associations[index] = LineAssociation(line.po_number, line.po_line_number)
        if line.unit_price_basis == 'net':
            net_prices[index] = line.unit_price
    command = EvaluateInvoiceCommand(invoice, str(job.owner_id), supplier_id=supplier_id,
                                    associations=associations, net_unit_prices=net_prices,
                                    origin='document-extraction')
    record = EvaluateInvoiceUseCase(_BorrowedTransaction(transaction), clock=clock).execute(command)
    return record.invoice_id, record.evaluation_id, record.result.complete


class ProcessInvoice:
    def __init__(self, repository, storage, extractor, *, offload=None, deadline=90,
                 clock=lambda: datetime.now(timezone.utc), document_factory=ValidatedDocument):
        if not math.isfinite(deadline) or not 0 < deadline < repository.config.lease_seconds:
            raise ValueError('Extraction deadline must be positive and shorter than its lease')
        self.repository = repository
        self.storage = storage
        self.extractor = extractor
        self.offload = offload or BoundedOffload()
        self.deadline = deadline
        self.clock = clock
        self.document_factory = document_factory

    async def process_once(self):
        """At most one extraction and evaluation. Eligibility is decided in SQL."""
        claim = await self.offload(self.repository.claim)
        if claim is not None:
            failure = None
            try:
                data = await self.offload(self.storage.read, claim.job.storage_key)
                document = self.document_factory(data, claim.job.metadata)
                # The lease already includes storage/offload time. Leave one
                # second for settling the result; if no usable budget remains,
                # do not contact Azure or try to fail an expired claim. The
                # ordinary claim path will recover the durable lease.
                remaining = (claim.expires_at - self.clock()).total_seconds() - 1
                if remaining <= 0:
                    return True
                async with asyncio.timeout(min(self.deadline, remaining)):
                    result = await self.extractor.extract(document)
            except TimeoutError:
                failure = ExtractionError('timeout', 'Extraction timed out', retryable=True)
            except ExtractionError as error:
                failure = error
            except asyncio.CancelledError:
                raise  # Lease remains durable and is recovered after expiry.
            except Exception:
                failure = ExtractionError('processing_failed', 'Processing failed', retryable=False)
            # Storage failures and provider cancellation/timeout cleanup can
            # themselves consume the lease. SQL fencing remains authoritative
            # for races after this local expiry check.
            if self.clock() >= claim.expires_at:
                return True
            try:
                if failure is None:
                    await self.offload(self.repository.extracted, claim, result)
                else:
                    floor = (self.clock() + timedelta(seconds=failure.retry_after)
                             if failure.retry_after is not None else None)
                    await self.offload(self.repository.failed, claim, failure.code,
                                       retryable=failure.retryable, retry_after=floor)
            except StaleLease:
                pass
        evaluation = await self.offload(self.repository.claim_evaluation)
        if evaluation is not None:
            try:
                await self.offload(self.repository.complete, evaluation,
                                   lambda tx, job: evaluate_draft(tx, job, self.clock))
            except StaleLease:
                pass
        return claim is not None or evaluation is not None

    tick = process_once

    async def run(self, idle_seconds=1):
        while True:
            try:
                worked = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # DB/storage faults must not crash lifespan or cause a hot loop.
                worked = False
            if not worked:
                await asyncio.sleep(idle_seconds)
