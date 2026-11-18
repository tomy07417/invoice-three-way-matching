"""Public initial evaluation, reevaluation and approval on disposable PostgreSQL."""
from copy import deepcopy

from test_invoice_list import login
from test_review_api import review, decision, headers


def test_description_link_survives_initial_evaluation_and_reevaluation(review):
    client, _, seed = review
    case = seed(explicit=False)
    csrf = login(client, 'analyst')
    initial = client.get(f'/review/{case}').json()
    original = deepcopy(initial['original_extraction'])
    expected = [{'line_index': 0, 'po_number': 'PO-1', 'po_line_number': 3}]
    assert initial['evaluation']['input_snapshot']['associations'] == expected
    assert initial['comparison_rows'][0]['association_method'] == 'description'
    assert initial['comparison_rows'][0]['quantity_available'] == '5'
    rules = initial['evaluation']['result']['rules']
    assert not any(rule['code'] == 'LINE_ASSOCIATION' and rule['outcome'] == 'HOLD' for rule in rules)
    assert any(rule['code'] == 'UNIT_PRICE' and rule['outcome'] == 'HOLD' for rule in rules)
    assert initial['extraction']['lines'][0]['po_number'] is None
    response = client.post(f'/review/{case}/reevaluations', headers=headers(csrf, 0),
                           json={'comment': 'Recheck the same reliable description association'})
    assert response.status_code == 200
    after = response.json()
    assert after['evaluation']['input_snapshot']['associations'] == expected
    assert after['comparison_rows'][0]['association_method'] == 'description'
    assert after['original_extraction'] == after['extraction'] == original
    assert after['evaluation']['evaluation_id'] != initial['evaluation']['evaluation_id']
    assert client.get(f'/invoices/{case}').json()['evaluation']['evaluation_id'] == initial['evaluation']['evaluation_id']


def test_inferred_approval_rechecks_fresh_received_quantity_and_no_write_on_failure(review):
    client, _, seed = review
    first = seed('INFERRED-FIRST', explicit=False)
    second = seed('INFERRED-SECOND', quantity='4', total='48', explicit=False)
    csrf = login(client, 'analyst')
    response = decision(client, csrf, first, 0)
    assert response.status_code == 200
    assert response.json()['operational_state'] == 'APPROVED'
    assert not response.json()['payment_blocked']
    before = client.get(f'/review/{second}/history').json()
    denied = decision(client, csrf, second, 0)
    assert denied.status_code == 409
    assert denied.json()['error']['code'] == 'insufficient_available_quantity'
    assert client.get(f'/review/{second}/history').json() == before
    assert client.get(f'/review/{second}').json()['revision'] == 0
    refreshed = client.post(f'/review/{second}/reevaluations', headers=headers(csrf, 0),
                            json={'comment': 'Observe authoritative consumed quantity'})
    assert refreshed.status_code == 200
    row = refreshed.json()['comparison_rows'][0]
    assert row['quantity_previously_approved'] == '2'
    assert row['quantity_available'] == '3'


def test_description_resolution_never_invents_net_basis_or_changes_extraction(review):
    client, _, seed = review
    case = seed(explicit=False, basis='unknown')
    csrf = login(client, 'analyst')
    initial = client.get(f'/review/{case}').json()
    assert initial['comparison_rows'][0]['association_method'] == 'description'
    assert initial['comparison_rows'][0]['invoice_net_unit_price'] is None
    assert initial['evaluation']['result']['final_status'] is None
    assert any(rule['code'] == 'UNIT_PRICE' and rule['outcome'] == 'PENDING'
               for rule in initial['evaluation']['result']['rules'])
    before = client.get(f'/review/{case}/history').json()
    response = decision(client, csrf, case, 0)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'incomplete_evidence'
    assert client.get(f'/review/{case}/history').json() == before
    after = client.get(f'/review/{case}').json()
    assert after['extraction'] == after['original_extraction'] == initial['extraction']
    assert after['revision'] == 0 and after['payment_blocked']
