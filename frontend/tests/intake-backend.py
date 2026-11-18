"""Controlled-provider browser fixture. Never imports Azure or uses an application DB.

Run only through intake-browser.mjs. TEST_DATABASE_URL must explicitly identify a
local test_ database; a unique database and private directory are owned here.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import json
import os
import signal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))

from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
import uvicorn

from app.application.dtos.auth import Role
from app.application.dtos.invoice_documents import ExtractionResult, InvoiceDraft, InvoiceLineDraft, TokenUsage
from app.application.dtos.processing import ProcessingConfig
from app.application.dtos.reference_data import ReferenceData
from app.application.use_cases.import_reference_data import import_reference_data
from app.application.use_cases.process_invoice import BoundedOffload, ProcessInvoice
from app.application.use_cases.sessions import SessionService
from app.domain.entities import Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceivedNote, GoodsReceivedNoteLine
from app.infrastructure.extraction.document_validator import LocalDocumentValidator
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.processing_repository import SqlProcessingRepository
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from app.infrastructure.processing.service import InvoiceService
from app.infrastructure.storage.local_documents import LocalDocumentStorage
from app.main import create_app


class ControlledExtractor:
    def __init__(self, *, review=False):
        self.count = 0
        self.review = review

    async def extract(self, document):
        self.count += 1
        number = self.count
        await asyncio.sleep(8 if number == 1 and not self.review else 0.1)
        quantity = Decimal('2') if self.review else Decimal('6')
        price = Decimal('12.000000') if self.review else Decimal('10.000000')
        currency = 'USD'
        if self.review and document.metadata.file_name == 'manager.png':
            quantity, price = Decimal('1'), Decimal('5001.000000')
        if self.review and document.metadata.file_name == 'eur.png':
            quantity, price, currency = Decimal('1'), Decimal('1.000000'), 'EUR'
        total = quantity * price
        draft = InvoiceDraft(
            'Browser Supplier', 'browser-tax', f'BROWSER-{number}', date(2026, 10, 2), None,
            ('PO-BROWSER',), currency, total, Decimal('0.000000'), total,
            (InvoiceLineDraft('Browser item', quantity, price, total,
                              '3', 'PO-BROWSER', 3, 'net'),))
        return ExtractionResult(True, draft, ('Controlled extraction warning.',), TokenUsage(10, 10))


def main():
    # Also unwind owned resources if terminated during migration/provisioning,
    # before Uvicorn installs its own graceful signal handlers.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    configured = os.environ.get('TEST_DATABASE_URL')
    if not configured:
        raise ValueError('TEST_DATABASE_URL is required; no application database fallback')
    url = make_url(configured)
    if (url.get_backend_name() != 'postgresql' or url.host != '127.0.0.1'
            or url.port != 55472 or not url.database or not url.database.startswith('test_')):
        raise ValueError('Only the isolated local PostgreSQL service and test_ databases are allowed')
    url = url.set(drivername='postgresql+psycopg')
    name = f'test_browser_{uuid4().hex}'
    admin = create_engine(url, isolation_level='AUTOCOMMIT', connect_args={'connect_timeout': 3})
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        owned_url = url.set(database=name)
        # The migration API reads only this explicitly overwritten, owned URL.
        os.environ['DATABASE_URL'] = owned_url.render_as_string(hide_password=False)
        config = Config(str(BACKEND / 'alembic.ini'))
        config.set_main_option('script_location', str(BACKEND / 'migrations'))
        command.upgrade(config, 'head')
        engine = create_engine(owned_url, connect_args={'connect_timeout': 3})
        day = date(2026, 10, 1)
        reference = ReferenceData(
            (Supplier('BROWSER', 'Browser Supplier', 'browser-tax', 'USD', 'active', 'Synthetic address'),),
            (PurchaseOrder('PO-BROWSER', 'BROWSER', day, 'USD', 'open',
                           (PurchaseOrderLine(3, 'SKU', 'Browser item', Decimal('10'), Decimal('10')),)),),
            (GoodsReceivedNote('GRN-BROWSER', 'PO-BROWSER', day,
                              (GoodsReceivedNoteLine(3, Decimal('5')),)),))
        import_reference_data(reference, SqlReferenceDataRepository(engine))
        sessions = SessionService(SqlAuthRepository(engine))
        review_mode = os.environ.get('AP_REVIEW_FIXTURE') == '1'
        accounts = [('browser_owner', Role.INTAKE), ('browser_other', Role.INTAKE)]
        if review_mode:
            accounts.extend([('browser_analyst', Role.AP_ANALYST), ('browser_manager', Role.AP_MANAGER)])
        for username, role in accounts:
            sessions.provision(username, 'browser test password', role)
        with TemporaryDirectory(prefix='intake-fixture-') as scratch:
            png = Path(scratch) / 'invoice.png'
            Image.new('RGB', (16, 16), color='white').save(png, format='PNG')
            offload = BoundedOffload(4)
            repository = SqlProcessingRepository(engine, config=ProcessingConfig(max_pending=64))
            storage = LocalDocumentStorage(Path(scratch) / 'private')
            service = InvoiceService(sessions, repository, LocalDocumentValidator(), storage,
                                     offload=offload, estimated_tokens=100)
            worker = ProcessInvoice(repository, storage, ControlledExtractor(review=review_mode), offload=offload)
            application = create_app(invoice_service=service, processing_enabled=False)

            @asynccontextmanager
            async def lifespan(app):
                task = asyncio.create_task(worker.run(idle_seconds=0.2))
                # Uvicorn's subsequent standard listening line is the bind/readiness signal.
                print('INTAKE_FIXTURE ' + json.dumps({'png': str(png)}), flush=True)
                try:
                    yield
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            application.router.lifespan_context = lifespan

            @application.middleware('http')
            async def observable_upload(request, call_next):
                response = await call_next(request)
                if request.method == 'POST' and request.url.path == '/invoices':
                    await asyncio.sleep(0.6)
                return response

            uvicorn.run(application, host='127.0.0.1', port=3211, access_log=False, log_level='info')
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Driver/validation exceptions can include private URLs or document values.
        print('Intake fixture failed; connection/document details withheld.', file=sys.stderr)
        sys.exit(1)
