"""Manager-only public audit contract using real upload/processing/review facts."""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from test_review_api import review, headers, decision
from test_invoice_list import login


@pytest.fixture
def audit(review, migrated_engine):
    from app.application.use_cases.audit import AuditService
    from app.application.use_cases.sessions import SessionService
    from app.infrastructure.persistence.audit_repository import SqlAuditRepository
    from app.infrastructure.persistence.auth_repository import SqlAuthRepository
    from app.interfaces.api.audit import build_audit_router
    client, app, seed = review
    app.include_router(build_audit_router(AuditService(SessionService(SqlAuthRepository(migrated_engine)),
                                                     SqlAuditRepository(migrated_engine))))
    yield client, app, seed


def get_events(client, case, **filters):
    response = client.get(f'/audit/{case}', params=filters)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    return response.json()


def test_manager_audit_projects_original_truth_and_keeps_existing_history(audit):
    client, _, seed = audit
    case = seed()
    login(client, 'manager')
    before = client.get(f'/review/{case}/history').json()
    processing = client.get(f'/invoices/{case}/processing').json()
    page = get_events(client, case)
    assert page['revision'] == 0 and page['total'] == len(page['events'])
    assert (page['offset'], page['limit']) == (0, 50)
    events = page['events']
    assert {'creation', 'extraction', 'evaluation', 'state_change'} <= {e['kind'] for e in events}
    extraction, = [e for e in events if e['kind'] == 'extraction']
    assert extraction['actor'] == {'id': None, 'name': None, 'role': None, 'type': 'system'}
    assert extraction['provenance']['source'] == 'processing_events'
    assert extraction['provenance']['derived'] is False
    assert extraction['at'] == next(e['at'] for e in processing['events'] if e['state'] == 'EXTRACTED')
    assert extraction['current'] and extraction['extraction_version'] == 1
    evaluation, = [e for e in events if e['kind'] == 'evaluation']
    assert evaluation['evidence']['evaluation']['result']['final_status'] == 'Held for Review'
    # The evaluation records an initiating owner, not an inferred system actor.
    assert evaluation['actor']['id'] is not None and evaluation['actor']['type'] == 'recorded'
    assert evaluation['evidence']['evaluation']['origin'] == 'document-extraction'
    assert client.get(f'/review/{case}/history').json() == before
    assert client.get(f'/invoices/{case}/processing').json() == processing
    serialized = str(page).lower()
    for forbidden in ('storage_key', 'password_hash', 'csrf_token', 'lease_token', 'response_id', 'configuration_endpoint'):
        assert forbidden not in serialized


def test_exact_version_diffs_decisions_and_reevaluation_remain_historical(audit):
    client, _, seed = audit
    case = seed()
    csrf = login(client, 'analyst')
    original = client.get(f'/review/{case}').json()
    assert decision(client, csrf, case, 0).status_code == 200
    draft = deepcopy(original['extraction'])
    draft['lines'][0]['quantity'] = '3.000000000000000001'
    draft['total_amount'] = '36.000000000000000012'
    assert client.patch(f'/review/{case}', headers=headers(csrf, 1),
                        json={'invoice': draft, 'comment': 'Correct exact quantity'}).status_code == 200
    login(client, 'manager')
    page = get_events(client, case)
    correction, = [e for e in page['events'] if e['kind'] == 'correction']
    assert correction['revision'] == 2 and correction['extraction_version'] == 2
    assert correction['actor']['name'] == 'analyst'
    assert correction['provenance']['derived'] is False
    diff = next(d for d in correction['diffs'] if d['field'] == 'lines.0.quantity')
    assert diff == {'field': 'lines.0.quantity', 'before': '2', 'after': '3.000000000000000001',
                    'before_known': True, 'derived': True, 'source': 'review_extraction_versions'}
    assert not next(e for e in page['events'] if e['kind'] == 'extraction')['current']
    assert not next(e for e in page['events'] if e['kind'] == 'decision')['current']
    assert not next(e for e in page['events'] if e['kind'] == 'evaluation')['current']
    csrf = login(client, 'analyst')
    assert client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 2),
                       json={'comment': 'Reevaluate corrected version'}).status_code == 200
    login(client, 'manager')
    page = get_events(client, case)
    reevaluation, = [e for e in page['events'] if e['kind'] == 'reevaluation']
    assert reevaluation['current'] and reevaluation['extraction_version'] == 2
    assert reevaluation['evidence']['evaluation']['origin'] == 'ap-review-reevaluation'
    assert reevaluation['evidence']['evaluation']['input_snapshot'] != next(
        e for e in page['events'] if e['kind'] == 'evaluation')['evidence']['evaluation']['input_snapshot']
    assert 'approval_reversal' in {e['kind'] for e in page['events']}
    assert client.get(f'/invoices/{case}').json()['evaluation']['evaluation_id'] == original['evaluation']['evaluation_id']


def test_escalation_credit_note_and_state_changes(audit):
    client, _, seed = audit
    case = seed(total='5000.01')
    csrf = login(client, 'analyst')
    assert client.post(f'/review/{case}/escalations', headers=headers(csrf, 0),
                       json={'comment': 'Above evaluated limit'}).status_code == 200
    csrf = login(client, 'manager')
    assert decision(client, csrf, case, 1, action='request_credit_note').status_code == 200
    page = get_events(client, case)
    assert {'escalation', 'credit_note_request', 'decision', 'state_change'} <= {e['kind'] for e in page['events']}
    credit, = [e for e in page['events'] if e['kind'] == 'credit_note_request']
    assert credit['current'] and credit['evidence']['action'] == 'request_credit_note'
    states = [e for e in page['events'] if e['kind'] == 'state_change' and e['revision'] is not None]
    assert any(any(d['after'] == 'ESCALATED' for d in e['diffs']) for e in states)
    assert any(any(d['after'] == 'CREDIT_NOTE_REQUESTED' for d in e['diffs']) for e in states)
    assert all(e['provenance']['derived'] for e in states)
    assert all(not d['before_known'] for e in states for d in e['diffs'])


def test_authorization_validation_filters_and_reads_never_change_case(audit):
    client, _, seed = audit
    case = seed()
    login(client, 'analyst')
    before = client.get(f'/review/{case}/history').json()
    processing = client.get(f'/invoices/{case}/processing').json()
    for username, expected in [('owner', 403), ('analyst', 403)]:
        login(client, username)
        denied = client.get(f'/audit/{case}', headers={'X-Role': 'AP_MANAGER', 'X-User-Id': '3'})
        assert denied.status_code == expected
        assert denied.headers['cache-control'] == 'private, no-store'
        assert denied.json()['error']['code'] == 'authorization_failed'
    client.cookies.clear()
    assert client.get(f'/audit/{case}').status_code == 401
    login(client, 'manager')
    all_events = get_events(client, case)['events']
    first = get_events(client, case, limit=1)
    second = get_events(client, case, limit=1, offset=1)
    assert first['events'] == all_events[:1] and second['events'] == all_events[1:2]
    assert first['total'] == second['total'] == len(all_events)
    assert get_events(client, case, offset=999)['events'] == []
    at = next(e['at'] for e in all_events if e['kind'] == 'creation')
    selected = get_events(client, case, kind='creation', time_from=at, time_to=at)['events']
    assert len(selected) == 1
    assert get_events(client, case, actor_id=selected[0]['actor']['id'])['events'] == [
        event for event in all_events if event['actor']['id'] == selected[0]['actor']['id']]
    assert get_events(client, case, actor_id='not-recorded')['events'] == []
    for query in ('limit=101', 'limit=0', 'offset=-1', 'kind=not-an-event', 'time_from=bad',
                  'time_from=2027-01-02T00:00:00Z&time_to=2027-01-01T00:00:00Z',
                  'time_from=2027-01-01T00:00:00', 'actor_id=' + 'x' * 129):
        result = client.get(f'/audit/{case}?{query}')
        assert result.status_code == 422, result.text
        assert set(result.json()['error']) == {'code', 'message', 'retryable', 'correlation_id'}
        assert result.json()['error']['message'] == 'Invalid audit request parameters'
    assert client.get(f'/audit/{uuid4()}').status_code == 404
    assert client.get('/audit/not-a-uuid').status_code == 422
    assert client.post(f'/audit/{case}').status_code == 405
    login(client, 'analyst')
    assert client.get(f'/review/{case}/history').json() == before
    assert client.get(f'/invoices/{case}/processing').json() == processing


def test_legacy_upload_does_not_reuse_synthesized_original_metadata(audit, migrated_engine):
    # Seed a legacy upload without historical processing rows; never delete or
    # rewrite real processing evidence to manufacture the metadata gap.
    from sqlalchemy.orm import Session
    from app.infrastructure.persistence.processing_models import UploadCase
    client, _, seed = audit
    original = seed()
    with Session(migrated_engine) as session, session.begin():
        source = session.get(UploadCase, UUID(original))
        values = {column.name: deepcopy(getattr(source, column.name)) for column in UploadCase.__table__.columns}
        legacy_id = uuid4()
        case = str(legacy_id)
        values.update(invoice_id=legacy_id, job_id=uuid4(), idempotency_key='legacy-upload')
        session.add(UploadCase(**values))
    csrf = login(client, 'analyst')
    assert decision(client, csrf, case, 0, action='reject').status_code == 200
    login(client, 'manager')
    before = client.get(f'/review/{case}/history').json()
    extraction, = [e for e in get_events(client, case)['events'] if e['kind'] == 'extraction']
    assert extraction['at'] is None
    assert extraction['actor'] == {'id': None, 'name': None, 'role': None, 'type': 'unknown'}
    assert extraction['provenance']['source'] == 'upload_cases.extraction'
    assert 'unknown' in extraction['provenance']['note'].lower()
    assert extraction['diffs'] == []
    assert client.get(f'/review/{case}/history').json() == before


def test_coherent_snapshot_while_public_correction_commits(audit, migrated_engine):
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    client, app, seed = audit
    case = seed()
    csrf = login(client, 'analyst')
    detail = client.get(f'/review/{case}').json()
    draft = deepcopy(detail['extraction'])
    draft['total_amount'] = '24.000000000000000001'
    with TestClient(app) as writer:
        csrf = login(writer, 'analyst')
        login(client, 'manager')
        committed = []

        def mutate_after_snapshot(connection, cursor, statement, parameters, context, executemany):
            if not committed and 'FROM upload_cases' in statement:
                committed.append(True)
                result = writer.patch(f'/review/{case}', headers=headers(csrf, 0),
                                      json={'invoice': draft, 'comment': 'Concurrent exact correction'})
                assert result.status_code == 200, result.text

        event.listen(migrated_engine, 'after_cursor_execute', mutate_after_snapshot)
        try:
            page = get_events(client, case)
        finally:
            event.remove(migrated_engine, 'after_cursor_execute', mutate_after_snapshot)
        assert committed == [True]
        assert page['revision'] == 0
        assert not any(e['kind'] == 'correction' for e in page['events'])
        assert next(e for e in page['events'] if e['kind'] == 'evaluation')['current']
        next_page = get_events(client, case)
        assert next_page['revision'] == 1
        assert next(e for e in next_page['events'] if e['kind'] == 'correction')['current']
        assert not next(e for e in next_page['events'] if e['kind'] == 'evaluation')['current']


def test_router_schema_and_missing_session_without_database():
    # Real SessionService rejects missing sessions before touching a repository.
    # No feature mock, DB connection or provider is needed for this public boundary.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.application.use_cases.audit import AuditService
    from app.application.use_cases.sessions import SessionService
    from app.infrastructure.persistence.audit_repository import SqlAuditRepository
    from app.infrastructure.persistence.auth_repository import SqlAuthRepository
    from app.interfaces.api.audit import build_audit_router
    app = FastAPI()
    app.include_router(build_audit_router(AuditService(SessionService(SqlAuthRepository(None)), SqlAuditRepository(None))))
    with TestClient(app) as client:
        result = client.get(f'/audit/{uuid4()}', headers={'X-Role': 'AP_MANAGER'})
        assert result.status_code == 401
        assert result.headers['cache-control'] == 'private, no-store'
        assert result.json()['error'] == {'code': 'authorization_failed', 'message': 'Authentication required',
            'retryable': False, 'correlation_id': result.headers['x-correlation-id']}
        assert client.get('/audit/invalid?limit=1000').status_code == 401
        operation = client.get('/openapi.json').json()['paths']['/audit/{invoice_id}']
        assert set(operation) == {'get'}
        parameters = {p['name']: p for p in operation['get']['parameters']}
        assert set(parameters) == {'invoice_id', 'offset', 'limit', 'kind', 'actor_id', 'time_from', 'time_to'}
        assert parameters['limit']['schema']['maximum'] == 100
        assert parameters['limit']['schema']['default'] == 50
