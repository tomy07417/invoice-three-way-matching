"""Manager settings through HTTP with real isolated PostgreSQL and persisted sessions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.dtos.auth import Role
from app.application.use_cases.sessions import SessionService
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.interfaces.api.sessions import COOKIE_NAME
from test_review_api import review  # Reuse the public upload/processing/review fixture.


@pytest.fixture
def parameters(migrated_engine):
    from app.application.use_cases.parameters import ParameterService
    from app.infrastructure.persistence.parameter_repository import SqlParameterRepository
    from app.interfaces.api.parameters import build_parameters_router

    sessions = SessionService(SqlAuthRepository(migrated_engine))
    identities = {}
    for role in Role:
        sessions.provision(role.value, 'long enough password', role)
        identities[role] = sessions.login(role.value, 'long enough password')
    app = FastAPI()
    app.include_router(build_parameters_router(ParameterService(sessions, SqlParameterRepository(migrated_engine))))
    with TestClient(app) as client:
        token, identity = identities[Role.AP_MANAGER]
        client.cookies.set(COOKIE_NAME, token)
        yield client, app, identities, identity.csrf_token


def current(client):
    response = client.get('/manager/parameters')
    assert response.status_code == 200
    assert response.headers['etag'] == f'"{response.json()["version_id"]}"'
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def history(client):
    response = client.get('/manager/parameters/history')
    assert response.status_code == 200
    return response.json()


def patch(client, csrf, version, body):
    return client.patch('/manager/parameters', headers={'X-CSRF-Token': csrf, 'If-Match': f'"{version}"'}, json=body)


def test_original_change_history_and_safe_noop(parameters):
    client, _, _, csrf = parameters
    original = current(client)
    assert Decimal(original['price_tolerance']) == Decimal('.02')
    assert Decimal(original['analyst_approval_limit']) == 5000
    assert original['currency'] == 'USD' and original['actor'] is None
    assert original['actor_reference'] == 'bootstrap'
    assert history(client)['items'][0]['before'] is None
    start = datetime.now(timezone.utc)
    result = patch(client, csrf, original['version_id'], {'price_tolerance': '0.12345678901234567890123456789', 'comment': 'Future only'})
    assert result.status_code == 200
    changed = current(client)
    assert result.json() == changed
    assert changed['price_tolerance'] == '0.12345678901234567890123456789'
    assert changed['analyst_approval_limit'] == original['analyst_approval_limit']
    assert start <= datetime.fromisoformat(changed['effective_from']) <= datetime.now(timezone.utc)
    assert changed['actor']['username'] == 'ap_manager' and changed['actor']['role'] == 'ap_manager'
    assert type(changed['actor']['actor_id']) is int
    events = history(client)
    assert events['total'] == 2
    event = events['items'][0]
    assert event['version_id'] == changed['version_id']
    assert event['before']['price_tolerance'] == original['price_tolerance']
    assert event['after']['price_tolerance'] == changed['price_tolerance']
    assert event['previous_version_id'] == original['version_id']
    assert event['comment'] == 'Future only' and event['actor'] == changed['actor']
    assert patch(client, csrf, changed['version_id'], {'price_tolerance': changed['price_tolerance']}).json() == changed
    assert history(client) == events
    text = str(changed) + str(events)
    for forbidden in ('password', 'csrf', 'token_hash', csrf):
        assert forbidden not in text


@pytest.mark.parametrize('body', [
    {'price_tolerance': 0.02}, {'price_tolerance': True}, {'price_tolerance': 'NaN'},
    {'price_tolerance': 'Infinity'}, {'price_tolerance': '-.01'}, {'price_tolerance': ' 0.1'},
    {'analyst_approval_limit': 5000.0}, {'analyst_approval_limit': '-1'},
    {'analyst_approval_limit': '-Infinity'}, {'analyst_approval_limit': 'sNaN'},
    {'analyst_approval_limit': None}, {'currency': 'EUR'}, {'actor': 'manager'},
    {'effective_from': '2020-01-01'}, {}, {'comment': 'comment alone'},
    {'price_tolerance': '0', 'comment': 2}, {'price_tolerance': '0', 'unknown': 'x'},
])
def test_invalid_parameters_do_not_change_public_state(parameters, body):
    client, _, _, csrf = parameters
    before, events = current(client), history(client)
    response = patch(client, csrf, before['version_id'], body)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'invalid_parameters'
    assert not response.json()['error']['retryable']
    assert response.json()['error']['correlation_id'] == response.headers['x-correlation-id']
    assert current(client) == before and history(client) == events


@pytest.mark.parametrize('values', [('0', '0'), ('100000000000000000000000.123456789', '999999999999999999999999.000000001')])
def test_zero_and_uncapped_exact_values(parameters, values):
    client, _, _, csrf = parameters
    before = current(client)
    response = patch(client, csrf, before['version_id'], dict(zip(('price_tolerance', 'analyst_approval_limit'), values)))
    assert response.status_code == 200
    assert response.json()['price_tolerance'] == values[0]
    assert response.json()['analyst_approval_limit'] == values[1]


def test_authorization_csrf_preconditions_and_bounded_input(parameters):
    client, _, identities, csrf = parameters
    before, events = current(client), history(client)
    for role in (Role.INTAKE, Role.AP_ANALYST):
        token, identity = identities[role]
        client.cookies.set(COOKIE_NAME, token)
        for path in ('/manager/parameters', '/manager/parameters/history'):
            assert client.get(path, headers={'X-Role': 'ap_manager'}).status_code == 403
        assert patch(client, identity.csrf_token, before['version_id'], {'price_tolerance': '0'}).status_code == 403
    client.cookies.clear()
    assert client.get('/manager/parameters', headers={'X-Role': 'ap_manager'}).status_code == 401
    client.cookies.set(COOKIE_NAME, identities[Role.AP_MANAGER][0])
    assert client.patch('/manager/parameters', json={'price_tolerance': '0'}).status_code == 403
    assert client.patch('/manager/parameters', headers={'X-CSRF-Token': csrf}, json={'price_tolerance': '0'}).status_code == 428
    for version in ('1', '*', '"-1"', '"999999999999999999999"'):
        assert client.patch('/manager/parameters', headers={'X-CSRF-Token': csrf, 'If-Match': version}, json={'price_tolerance': '0'}).status_code == 422
    assert patch(client, csrf, before['version_id'] + 10, {'price_tolerance': '0'}).status_code == 409
    assert patch(client, csrf, before['version_id'], {'price_tolerance': '0', 'comment': 'x' * 17000}).status_code == 413
    malformed = client.patch('/manager/parameters', headers={'X-CSRF-Token': csrf, 'If-Match': f'"{before["version_id"]}"', 'Content-Type': 'application/json'}, content='{')
    assert malformed.status_code == 422
    for query in ('limit=101', 'offset=-1', 'offset=2147483648', 'limit=0'):
        assert client.get('/manager/parameters/history?' + query).status_code == 422
    assert current(client) == before and history(client) == events


def test_same_version_concurrent_writer_leaves_no_losing_event(parameters):
    client, app, identities, csrf = parameters
    before = current(client)
    token = identities[Role.AP_MANAGER][0]
    def change(value):
        with TestClient(app) as contender:
            contender.cookies.set(COOKIE_NAME, token)
            response = patch(contender, csrf, before['version_id'], {'analyst_approval_limit': value})
            return response.status_code, response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ['1', '2']))
    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(body for status, body in results if status == 200)
    assert current(client) == winner
    events = history(client)
    assert events['total'] == 2 and events['items'][0]['version_id'] == winner['version_id']
    assert client.get('/manager/parameters/history?offset=1&limit=1').json()['items'] == events['items'][1:]
    assert client.get('/manager/parameters/history?offset=2&limit=1').json()['items'] == []


def test_future_evaluations_and_historic_approval_limit_snapshot(review, migrated_engine):
    from app.application.use_cases.parameters import ParameterService
    from app.infrastructure.persistence.parameter_repository import SqlParameterRepository
    from app.interfaces.api.parameters import build_parameters_router
    from test_invoice_list import login
    from test_review_api import headers

    client, app, seed = review
    sessions = SessionService(SqlAuthRepository(migrated_engine))
    app.include_router(build_parameters_router(ParameterService(sessions, SqlParameterRepository(migrated_engine))))
    intake_session = client.cookies.get(COOKIE_NAME)
    case = seed('BEFORE')
    login(client, 'analyst')
    old = client.get(f'/review/{case}').json()
    old_history = client.get(f'/review/{case}/history').json()
    assert old['eligibility']['allowed']
    csrf = login(client, 'manager')
    original = current(client)
    changed = patch(client, csrf, original['version_id'], {'price_tolerance': '0.5', 'analyst_approval_limit': '0'})
    assert changed.status_code == 200
    login(client, 'analyst')
    assert client.get(f'/review/{case}').json() == old
    assert client.get(f'/review/{case}/history').json() == old_history
    # Existing snapshot still grants the Analyst the original USD 5000 limit.
    from test_review_api import decision
    analyst_csrf = login(client, 'analyst')
    approved = decision(client, analyst_csrf, case, 0)
    assert approved.status_code == 200
    after_approval = client.get(f'/review/{case}/history').json()
    # seed captures the original Intake CSRF token. Restore its matching
    # persisted session, rather than logging in and issuing a different token.
    # This new upload/evaluation occurs after the committed parameter update.
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, intake_session)
    future_case = seed('AFTER', quantity='3', total='36')
    analyst_csrf = login(client, 'analyst')
    future = client.get(f'/review/{future_case}').json()
    assert future['evaluation']['configuration_id'] == changed.json()['version_id']
    assert future['evaluation']['result']['config']['analyst_approval_limit'] == '0'
    assert not future['eligibility']['allowed']
    assert future['evaluation']['result']['final_status'] == 'Auto-Pay'
    assert client.get(f'/review/{case}/history').json() == after_approval
    assert client.get(f'/review/{case}').json()['operational_state'] == 'APPROVED'
    reevaluated = client.post(f'/review/{case}/reevaluations', headers=headers(analyst_csrf, 1),
                              json={'comment': 'Explicitly apply the new settings'})
    assert reevaluated.status_code == 200
    assert reevaluated.json()['evaluation']['configuration_id'] == changed.json()['version_id']
    assert not reevaluated.json()['eligibility']['allowed']
    final_history = client.get(f'/review/{case}/history').json()['events']
    assert any(event.get('evaluation_id') == old['evaluation']['evaluation_id'] for event in final_history)


def test_parameter_module_structural_composition():
    """DI and OpenAPI remain inspectable without a database or provider."""
    from app.interfaces.api.parameters import build_parameters_router
    app = FastAPI()
    app.include_router(build_parameters_router(None))
    schema = app.openapi()
    assert set(schema['paths']) == {'/manager/parameters', '/manager/parameters/history'}
    assert set(schema['paths']['/manager/parameters']) == {'get', 'patch'}
    assert 'unchanged values create no event' in schema['paths']['/manager/parameters']['patch']['summary']
    from app.application.dtos.parameters import parse_parameters
    from app.domain.matching.types import MatchingConfig
    patch_value = parse_parameters({'price_tolerance': '0.12345678901234567890123456789'})
    assert patch_value.apply(MatchingConfig()).price_tolerance == Decimal('0.12345678901234567890123456789')
