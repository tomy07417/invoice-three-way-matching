import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from PIL import Image

from app.application.dtos.auth import Role
from app.application.dtos.invoice_documents import ExtractionResult, TokenUsage, InvoiceDraft, InvoiceLineDraft
from app.application.dtos.processing import ProcessingConfig
from app.application.dtos.reference_data import ReferenceData
from app.application.use_cases.import_reference_data import import_reference_data
from app.domain.entities import Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceivedNote, GoodsReceivedNoteLine
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from app.application.ports.invoice_documents import ExtractionError
from app.application.use_cases.sessions import SessionService
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.processing_repository import SqlProcessingRepository
from app.infrastructure.processing.service import InvoiceService
from app.application.use_cases.process_invoice import ProcessInvoice
from app.infrastructure.extraction.document_validator import LocalDocumentValidator
from app.infrastructure.storage.local_documents import LocalDocumentStorage
from app.main import create_app


def test_slow_storage_expiry_skips_provider_and_recovers_durably(migrated_engine):
    now = [datetime(2027, 1, 1, tzinfo=timezone.utc)]
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    sessions.provision('owner', 'long enough password', Role.INTAKE)
    repo = SqlProcessingRepository(migrated_engine, clock=lambda: now[0], random=lambda: .5)
    calls = []

    class Provider:
        async def extract(self, document):
            calls.append('provider')
            assert migrated_engine.pool.checkedout() == 0
            return ExtractionResult(False, None, (), TokenUsage(10, 10))

    with TemporaryDirectory() as scratch:
        storage = LocalDocumentStorage(Path(scratch) / 'private')
        service = InvoiceService(sessions, repo, LocalDocumentValidator(), storage, estimated_tokens=1000)
        with TestClient(create_app(invoice_service=service)) as client:
            identity = client.post('/sessions', json={'username': 'owner', 'password': 'long enough password'}).json()
            output = BytesIO()
            Image.new('RGB', (2, 2)).save(output, format='PNG')
            response = client.post('/invoices', files={'file': ('invoice.png', output.getvalue(), 'image/png')},
                                   headers={'X-CSRF-Token': identity['csrf_token'], 'Idempotency-Key': 'one'})
            assert response.status_code == 202
            invoice_id = response.json()['invoice_id']

            class SlowStorage:
                def read(self, key):
                    now[0] += timedelta(seconds=repo.config.lease_seconds + 1)
                    return storage.read(key)

            worker = ProcessInvoice(repo, SlowStorage(), Provider(), clock=lambda: now[0])
            assert asyncio.run(worker.process_once())
            assert calls == []
            # Recovery belongs to the ordinary durable claim path, not a stale
            # failed() write by the worker after its slow read.
            assert not asyncio.run(ProcessInvoice(repo, storage, Provider(), clock=lambda: now[0]).process_once())
            state = client.get(f'/invoices/{invoice_id}/processing').json()
            assert state['processing_status'] == 'RETRY_WAIT'
            assert state['attempts'] == 1
            assert state['attempt_history'][0]['error_code'] == 'lease_expired'
            assert state['automatic_outcome'] is None
            now[0] = datetime.fromisoformat(state['next_eligible_at'])
            assert asyncio.run(ProcessInvoice(repo, storage, Provider(), clock=lambda: now[0]).process_once())
            assert calls == ['provider']
            final = client.get(f'/invoices/{invoice_id}/processing').json()
            assert final['processing_status'] == 'EVALUATION_INCOMPLETE'
            assert final['attempts'] == 2
            assert len(list((Path(scratch) / 'private').iterdir())) == 1


def test_upload_retry_restart_and_private_api(migrated_engine):
    now = [datetime(2027, 1, 1, tzinfo=timezone.utc)]
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    sessions.provision('owner', 'long enough password', Role.INTAKE)
    sessions.provision('other', 'long enough password', Role.INTAKE)
    repo = SqlProcessingRepository(migrated_engine, clock=lambda: now[0], random=lambda: .5)
    output = BytesIO()
    Image.new('RGB', (2, 2)).save(output, format='PNG')
    content = output.getvalue()
    calls = []

    class Provider:
        async def extract(self, document):
            assert migrated_engine.pool.checkedout() == 0
            calls.append(1)
            if len(calls) == 1:
                raise ExtractionError('timeout', 'safe', retryable=True)
            return ExtractionResult(False, None, (), TokenUsage(10, 10))

    with TemporaryDirectory() as scratch:
        storage = LocalDocumentStorage(Path(scratch) / 'private')
        service = InvoiceService(sessions, repo, LocalDocumentValidator(), storage, estimated_tokens=1000)
        app = create_app(invoice_service=service)
        with TestClient(app) as client:
            files = {'file': ('invoice.png', content, 'image/png')}
            assert client.post('/invoices', files=files).status_code == 401
            identity = client.post('/sessions', json={'username': 'owner', 'password': 'long enough password'}).json()
            headers = {'X-CSRF-Token': identity['csrf_token'], 'Idempotency-Key': 'one'}
            assert client.post('/invoices', files=files).status_code == 403
            response = client.post('/invoices', files=files, headers=headers)
            assert response.status_code == 202
            invoice_id = response.json()['invoice_id']
            assert client.post('/invoices', files=files, headers=headers).json()['invoice_id'] == invoice_id
            assert len(list((Path(scratch) / 'private').iterdir())) == 1
            altered = BytesIO()
            Image.new('RGB', (3, 3)).save(altered, format='PNG')
            assert client.post('/invoices', files={'file': ('other.png', altered.getvalue(), 'image/png')}, headers=headers).status_code == 409
            assert len(list((Path(scratch) / 'private').iterdir())) == 1
            assert client.post('/invoices', files={'file': ('bad.png', b'bad', 'image/png')}, headers=headers).status_code == 422
            assert len(client.get('/invoices').json()['items']) == 1
            assert client.get(f'/invoices/{invoice_id}/document').content == content
            assert client.get('/invoices?status=QUEUED&limit=1&offset=0').json()['total'] == 1
            assert client.get('/invoices?limit=101').status_code == 422
            assert client.post('/invoices', content=b'broken', headers={**headers, 'Content-Type': 'multipart/form-data'}).status_code == 422
            assert len(client.get('/invoices').json()['items']) == 1
            service.validator.max_bytes = len(content) - 1
            assert client.post('/invoices', files=files, headers=headers).status_code == 422
            service.validator.max_bytes = 8 * 1024 * 1024
            assert len(list((Path(scratch) / 'private').iterdir())) == 1
            worker = ProcessInvoice(repo, storage, Provider(), clock=lambda: now[0])
            assert asyncio.run(worker.process_once())
            waiting = client.get(f'/invoices/{invoice_id}/processing').json()
            assert waiting['processing_status'] == 'RETRY_WAIT'
            assert not asyncio.run(worker.process_once())
            assert client.post(f'/invoices/{invoice_id}/retry', headers=headers).status_code == 202
            now[0] = datetime.fromisoformat(waiting['next_eligible_at'])
            restarted = ProcessInvoice(repo, storage, Provider(), clock=lambda: now[0])
            assert asyncio.run(restarted.process_once())
            detail = client.get(f'/invoices/{invoice_id}').json()
            assert detail['processing_status'] == 'EVALUATION_INCOMPLETE'
            assert detail['automatic_outcome'] is None
            assert detail['attempts'] == 2
            assert not asyncio.run(restarted.process_once())
            assert client.post(f'/invoices/{invoice_id}/retry', headers=headers).status_code == 409
            client.post('/sessions', json={'username': 'other', 'password': 'long enough password'})
            assert client.get('/invoices').json()['items'] == []
            assert client.get(f'/invoices/{invoice_id}').status_code == 404
            assert client.get(f'/invoices/{invoice_id}/document').status_code == 404


@pytest.mark.parametrize('basis,expected', [('net', 'Auto-Pay'), ('unknown', None)])
def test_exact_mapping_atomic_evaluation_and_restart(migrated_engine, basis, expected):
    d = Decimal
    day = date(2026, 10, 1)
    reference = ReferenceData(
        (Supplier('S1', 'Supplier', 'tax', 'USD', 'active', 'Address'),),
        (PurchaseOrder('PO-1', 'S1', day, 'USD', 'open',
                       (PurchaseOrderLine(3, 'SKU', 'Item', d('10'), d('10')),)),),
        (GoodsReceivedNote('GRN-1', 'PO-1', day, (GoodsReceivedNoteLine(3, d('5')),)),))
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    actor = sessions.provision('owner', 'long enough password', Role.INTAKE)
    repo = SqlProcessingRepository(migrated_engine, clock=lambda: now)
    draft = InvoiceDraft('Supplier', 'tax', 'INV-1', day, None, ('PO-1',), 'USD',
                         d('20.00'), d('0'), d('20.00'),
                         (InvoiceLineDraft('Item', d('2'), d('10'), d('20'), '3', 'PO-1', 3, basis),))

    class Provider:
        async def extract(self, document):
            assert migrated_engine.pool.checkedout() == 0
            return ExtractionResult(True, draft, (), TokenUsage(10, 10))

    with TemporaryDirectory() as scratch:
        storage = LocalDocumentStorage(Path(scratch) / 'private')
        service = InvoiceService(sessions, repo, LocalDocumentValidator(), storage, estimated_tokens=1000)
        with TestClient(create_app(invoice_service=service)) as client:
            identity = client.post('/sessions', json={'username': 'owner', 'password': 'long enough password'}).json()
            output = BytesIO()
            Image.new('RGB', (2, 2)).save(output, format='PNG')
            headers = {'X-CSRF-Token': identity['csrf_token'], 'Idempotency-Key': 'one'}
            response = client.post('/invoices', files={'file': ('invoice.png', output.getvalue(), 'image/png')}, headers=headers)
            assert response.status_code == 202
            invoice_id = response.json()['invoice_id']
            worker = ProcessInvoice(repo, storage, Provider(), clock=lambda: now)
            assert asyncio.run(worker.process_once())
            detail = client.get(f'/invoices/{invoice_id}').json()
            assert detail['automatic_outcome'] == expected
            assert detail['processing_status'] == ('COMPLETED' if expected else 'EVALUATION_INCOMPLETE')
            assert detail['extraction']['invoice']['subtotal'] == '20.00'
            assert detail['evaluation']['input_snapshot']['invoice']['invoice_number'] == 'INV-1'
            assert detail['findings']
            restarted_repo = SqlProcessingRepository(migrated_engine, clock=lambda: now)
            assert not asyncio.run(ProcessInvoice(restarted_repo, storage, Provider(), clock=lambda: now).process_once())
            assert client.get(f'/invoices/{invoice_id}').json() == detail
            # Public history through the existing transaction interface proves no
            # additional raw input/evaluation was created by the restarted tick.
            from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository
            with SqlAlchemyInvoiceEvaluationRepository(migrated_engine).transaction() as tx:
                record = detail['evaluation']
                assert len(tx.list_evaluations(record['invoice_id'])) == 1
                assert tx.get_invoice_history(exclude_invoice_id=record['invoice_id']) == ()
                assert tx.get_approved_quantities(('PO-1',)) == {}


def test_rate_limit_floor_exhaustion_and_queue_rejection(migrated_engine):
    now = [datetime(2027, 1, 1, tzinfo=timezone.utc)]
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    sessions.provision('owner', 'long enough password', Role.INTAKE)
    repo = SqlProcessingRepository(migrated_engine, config=ProcessingConfig(max_pending=1),
                                   clock=lambda: now[0], random=lambda: .5)
    calls = []

    class Provider:
        async def extract(self, document):
            calls.append(1)
            assert migrated_engine.pool.checkedout() == 0
            raise ExtractionError('rate_limited', 'safe', retryable=True, retry_after=100)

    with TemporaryDirectory() as scratch:
        storage = LocalDocumentStorage(Path(scratch) / 'private')
        service = InvoiceService(sessions, repo, LocalDocumentValidator(), storage, estimated_tokens=1000)
        with TestClient(create_app(invoice_service=service)) as client:
            identity = client.post('/sessions', json={'username': 'owner', 'password': 'long enough password'}).json()
            output = BytesIO()
            Image.new('RGB', (2, 2)).save(output, format='PNG')
            files = {'file': ('invoice.png', output.getvalue(), 'image/png')}
            headers = {'X-CSRF-Token': identity['csrf_token'], 'Idempotency-Key': 'one'}
            response = client.post('/invoices', files=files, headers=headers)
            assert response.status_code == 202
            invoice_id = response.json()['invoice_id']
            before = client.get('/invoices').json()
            assert client.post('/invoices', files=files, headers={**headers, 'Idempotency-Key': 'two'}).status_code == 429
            assert client.get('/invoices').json() == before
            assert len(list((Path(scratch) / 'private').iterdir())) == 1
            worker = ProcessInvoice(repo, storage, Provider(), clock=lambda: now[0])
            for attempt in range(1, 4):
                started = now[0]
                assert asyncio.run(worker.process_once())
                state = client.get(f'/invoices/{invoice_id}/processing').json()
                assert state['attempts'] == attempt
                if attempt < 3:
                    assert state['processing_status'] == 'RETRY_WAIT'
                    eligible = datetime.fromisoformat(state['next_eligible_at'])
                    assert eligible > started + timedelta(seconds=100)
                    assert client.post(f'/invoices/{invoice_id}/retry', headers=headers).status_code == 202
                    assert not asyncio.run(worker.process_once())
                    now[0] = eligible
                else:
                    assert state['processing_status'] == 'EXTRACTION_FAILED'
                    assert client.post(f'/invoices/{invoice_id}/retry', headers=headers).status_code == 409
                    assert not asyncio.run(worker.process_once())
            assert len(calls) == 3
            assert client.get(f'/invoices/{invoice_id}').json()['automatic_outcome'] is None
