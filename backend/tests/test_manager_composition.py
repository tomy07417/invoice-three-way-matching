"""Composition seam checks; detailed Manager behavior is tested through the real APIs."""
from fastapi.testclient import TestClient

from app.main import create_app
from test_invoice_list import login
from test_review_api import review


def test_processing_application_mounts_manager_and_audit_services(review):
    client, _, seed = review
    case = seed('MANAGER-COMPOSITION')
    assert client.get('/manager/parameters').status_code == 403
    assert client.get(f'/audit/{case}').status_code == 403
    login(client, 'manager')
    parameters = client.get('/manager/parameters')
    assert parameters.status_code == 200
    assert parameters.json()['currency'] == 'USD'
    assert parameters.headers['etag'] == f'"{parameters.json()["version_id"]}"'
    history = client.get('/manager/parameters/history')
    assert history.status_code == 200 and history.json()['total'] == 1
    audit = client.get(f'/audit/{case}')
    assert audit.status_code == 200
    assert audit.json()['invoice_id'] == case
    assert any(event['kind'] == 'creation' for event in audit.json()['events'])
    assert audit.headers['cache-control'] == 'private, no-store'


def test_explicit_manager_composition_keeps_lazy_health_and_existing_routes():
    # Router construction must not require a live database, session or provider.
    application = create_app(parameter_service=object(), audit_service=object(), processing_enabled=False)
    paths = application.openapi()['paths']
    assert {'/manager/parameters', '/manager/parameters/history', '/audit/{invoice_id}'} <= set(paths)
    with TestClient(application) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        assert client.get('/purchase-orders/missing').status_code == 503
