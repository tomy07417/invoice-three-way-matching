"""Public intake list contract against isolated, migrated PostgreSQL."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.application.dtos.auth import Role
from app.application.dtos.invoice_documents import ExtractionResult, InvoiceDraft, InvoiceLineDraft, TokenUsage
from app.application.dtos.reference_data import ReferenceData
from app.application.ports.invoice_documents import ExtractionError
from app.application.use_cases.import_reference_data import import_reference_data
from app.application.use_cases.process_invoice import ProcessInvoice
from app.application.use_cases.sessions import SessionService
from app.domain.entities import Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceivedNote, GoodsReceivedNoteLine
from app.infrastructure.extraction.document_validator import LocalDocumentValidator
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationTransaction
from app.infrastructure.persistence.processing_repository import SqlProcessingRepository
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from app.infrastructure.processing.service import InvoiceService
from app.infrastructure.storage.local_documents import LocalDocumentStorage
from app.main import create_app


SUMMARY_FIELDS = ('invoice_number', 'supplier_name', 'invoice_date', 'total_amount', 'currency')


@pytest.fixture
def intake(migrated_engine):
    now = [datetime(2027, 1, 1, tzinfo=timezone.utc)]
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    for username in ('owner', 'other'):
        sessions.provision(username, 'long enough password', Role.INTAKE)
    repo = SqlProcessingRepository(migrated_engine, clock=lambda: now[0])
    with TemporaryDirectory() as scratch:
        storage = LocalDocumentStorage(Path(scratch) / 'private')
        service = InvoiceService(sessions, repo, LocalDocumentValidator(), storage, estimated_tokens=1000)
        with TestClient(create_app(invoice_service=service)) as client:
            yield client, repo, storage, now


def login(client, username='owner'):
    response = client.post('/sessions', json={'username': username, 'password': 'long enough password'})
    assert response.status_code == 200
    return response.json()['csrf_token']


def upload(client, csrf, key):
    output = BytesIO()
    Image.new('RGB', (2, 2)).save(output, format='PNG')
    response = client.post('/invoices', files={'file': ('invoice.png', output.getvalue(), 'image/png')},
                           headers={'X-CSRF-Token': csrf, 'Idempotency-Key': key})
    assert response.status_code == 202
    return response.json()


def process(repo, storage, now, result):
    class ControlledExtractor:
        async def extract(self, document):
            if isinstance(result, ExtractionError):
                raise result
            return result
    assert asyncio.run(ProcessInvoice(repo, storage, ControlledExtractor(), clock=lambda: now[0]).process_once())


@pytest.mark.parametrize('variant,expected', [
    ('complete', 'Auto-Pay'), ('excess', 'Held for Review'),
    ('missing_po', 'Rejected'), ('unknown_basis', None),
])
def test_list_projects_persisted_extraction_and_final_outcome(intake, migrated_engine, monkeypatch, variant, expected):
    client, repo, storage, now = intake
    day = date(2026, 10, 1)
    reference = ReferenceData(
        (Supplier('S1', 'Supplier', 'tax', 'USD', 'active', 'Address'),),
        (PurchaseOrder('PO-1', 'S1', day, 'USD', 'open',
                       (PurchaseOrderLine(3, 'SKU', 'Item', Decimal('10'), Decimal('10')),)),),
        (GoodsReceivedNote('GRN-1', 'PO-1', day, (GoodsReceivedNoteLine(3, Decimal('5')),)),))
    import_reference_data(reference, SqlReferenceDataRepository(migrated_engine))
    line = InvoiceLineDraft('Item', Decimal('2'), Decimal('10'), Decimal('20'), '3', 'PO-1', 3, 'net')
    if variant == 'excess':
        line = replace(line, quantity=Decimal('6'), line_total=Decimal('60'))
    if variant == 'unknown_basis':
        line = replace(line, unit_price_basis='unknown')
    draft = InvoiceDraft('Supplier', 'tax', ' INV-001 ', day, None,
                         () if variant == 'missing_po' else ('PO-1',), 'USD',
                         Decimal('20.00'), Decimal('0.00'), Decimal('20.00'), (line,))
    csrf = login(client)
    admitted = upload(client, csrf, 'evaluated')
    process(repo, storage, now, ExtractionResult(True, draft, (), TokenUsage(10, 10), 'private-provider-id'))
    detail_url = f"/invoices/{admitted['invoice_id']}"
    before = client.get(detail_url).json()
    processing_before = client.get(detail_url + '/processing').json()
    assert before['automatic_outcome'] == expected
    assert before['processing_status'] == ('COMPLETED' if expected else 'EVALUATION_INCOMPLETE')
    # Additional evaluated row is deliberately outside the requested page.
    now[0] += timedelta(minutes=2)
    second = upload(client, csrf, 'second')
    process(repo, storage, now, ExtractionResult(True, replace(draft, invoice_number='INV-002'), (), TokenUsage(10, 10)))
    evaluation_reads = []
    original = SqlAlchemyInvoiceEvaluationTransaction.get_evaluation

    def observed_read(tx, evaluation_id):
        evaluation_reads.append(str(evaluation_id))
        return original(tx, evaluation_id)

    monkeypatch.setattr(SqlAlchemyInvoiceEvaluationTransaction, 'get_evaluation', observed_read)
    response = client.get('/invoices?offset=0&limit=1')
    assert response.status_code == 200
    page = response.json()
    assert (page['total'], page['offset'], page['limit']) == (2, 0, 1)
    item, = page['items']
    assert item['automatic_outcome'] == expected
    assert item['invoice_id'] == admitted['invoice_id']
    assert {field: item[field] for field in SUMMARY_FIELDS} == {
        'invoice_number': ' INV-001 ', 'supplier_name': 'Supplier',
        'invoice_date': '2026-10-01', 'total_amount': '20.00', 'currency': 'USD',
    }
    assert evaluation_reads == [str(before['evaluation']['evaluation_id'])]
    assert item['created_at'] == admitted['created_at']
    assert item['processing_status'] == before['processing_status']
    assert item['error'] is None
    assert set(item) == set(admitted) | set(SUMMARY_FIELDS)
    assert client.get(detail_url).json() == before
    assert client.get(detail_url + '/processing').json() == processing_before
    assert client.get('/invoices?offset=1&limit=1').json()['items'][0]['invoice_id'] == second['invoice_id']
    assert client.get('/invoices?offset=2&limit=1').json() == dict(items=[], total=2, offset=2, limit=1)


@pytest.mark.parametrize('extraction', [
    ExtractionResult(False, None, (), TokenUsage(10, 10)),
    ExtractionResult(True, InvoiceDraft(None, None, None, None, None, None, None, None, None, None, ()),
                     (), TokenUsage(10, 10)),
    ExtractionError('provider_unavailable', 'safe', retryable=False),
])
def test_list_null_unknowns_and_preserves_auth_owner_filters_and_state(intake, extraction):
    client, repo, storage, now = intake
    assert client.get('/invoices').status_code == 401
    csrf = login(client)
    admitted = upload(client, csrf, 'first')
    queued_response = client.get('/invoices?status=QUEUED&offset=0&limit=1')
    assert queued_response.status_code == 200
    queued = queued_response.json()
    assert (queued['total'], queued['offset'], queued['limit']) == (1, 0, 1)
    item, = queued['items']
    assert {field: item[field] for field in SUMMARY_FIELDS} == dict.fromkeys(SUMMARY_FIELDS)
    assert {key: value for key, value in item.items() if key not in SUMMARY_FIELDS} == admitted
    process(repo, storage, now, extraction)
    detail_url = f"/invoices/{admitted['invoice_id']}"
    before = client.get(detail_url).json()
    processing_before = client.get(detail_url + '/processing').json()
    status = 'EXTRACTION_FAILED' if isinstance(extraction, ExtractionError) else 'EVALUATION_INCOMPLETE'
    response = client.get(f'/invoices?status={status}&offset=0&limit=1')
    assert response.status_code == 200
    page = response.json()
    assert (page['total'], page['offset'], page['limit']) == (1, 0, 1)
    item, = page['items']
    assert item['processing_status'] == status
    assert item['automatic_outcome'] is None
    assert {field: item[field] for field in SUMMARY_FIELDS} == dict.fromkeys(SUMMARY_FIELDS)
    assert item['error'] == before['error']
    assert client.get('/invoices?status=QUEUED').json() == dict(items=[], total=0, offset=0, limit=20)
    for query in ('limit=101', 'limit=0', 'offset=-1'):
        assert client.get('/invoices?' + query).status_code == 422
    assert client.get(detail_url).json() == before
    assert client.get(detail_url + '/processing').json() == processing_before
    other_csrf = login(client, 'other')
    assert client.get('/invoices').json() == dict(items=[], total=0, offset=0, limit=20)
    assert client.get(detail_url).status_code == 404
    assert client.get(detail_url + '/document').status_code == 404
    now[0] += timedelta(minutes=2)
    other = upload(client, other_csrf, 'other')
    assert client.get('/invoices').json()['items'][0]['invoice_id'] == other['invoice_id']
    login(client)
    own_page = client.get('/invoices').json()
    assert own_page['total'] == 1
    assert own_page['items'] == page['items']
    assert client.get(detail_url).json() == before
    assert client.get(detail_url + '/processing').json() == processing_before
