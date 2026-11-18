"""Accounting evidence crosses the public extraction/review contracts unchanged."""
import asyncio
import json
from copy import deepcopy
from decimal import Decimal
import httpx
import pytest

from app.application.dtos.review import parse_review_draft, review_value, ReviewError
from app.infrastructure.extraction.azure_openai import AzureOpenAIExtractor
from test_azure_extraction import draft as legacy_draft, envelope, document, ENDPOINT, SECRET, DEPLOYMENT


def draft():
    raw = legacy_draft()
    raw['lines'][0]['quantity'] = '2.0000'
    return raw

EVIDENCE = {'taxes_separately_stated': True, 'adjustments': [
    {'kind': 'discount', 'stage': 'before_subtotal', 'amount': '-10.00', 'line_index': 0}]}


def extract(raw):
    async def run():
        calls = []
        def handle(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json=envelope({'is_invoice': True, 'invoice': raw, 'warnings': []}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await AzureOpenAIExtractor(client, endpoint=ENDPOINT, api_key=SECRET, deployment=DEPLOYMENT).extract(document())
        assert len(calls) == 1
        schema = calls[0]['text']['format']['schema']['properties']['invoice']['anyOf'][0]
        assert 'accounting_evidence' in schema['properties']
        assert 'No arithmetic inference' in calls[0]['input'][0]['content'][0]['text']
        return result
    return asyncio.run(run())


def test_present_accounting_evidence_survives_extraction_and_public_review_roundtrip():
    raw = draft(); raw['accounting_evidence'] = deepcopy(EVIDENCE)
    result = extract(raw)
    assert result.invoice.accounting_evidence.adjustments[0].amount == Decimal('-10.00')
    public = review_value(result.invoice)
    assert public == raw
    assert review_value(parse_review_draft(public)) == public
    assert public['lines'][0]['unit_price_basis'] == 'unknown'


def test_old_extraction_and_review_payloads_keep_their_exact_shape():
    raw = draft()
    result = extract(raw)
    assert result.invoice.accounting_evidence is None
    assert review_value(result.invoice) == raw
    assert review_value(parse_review_draft(raw)) == raw


@pytest.mark.parametrize('evidence', [
    {'taxes_separately_stated': None, 'adjustments': None},
    {'taxes_separately_stated': False, 'adjustments': []},
    {'taxes_separately_stated': True, 'adjustments': []},
])
def test_missing_versus_known_empty_evidence_is_not_conflated(evidence):
    raw = draft(); raw['accounting_evidence'] = evidence
    assert review_value(parse_review_draft(raw)) == raw


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(taxes_separately_stated=1),
    lambda e: e['adjustments'][0].update(amount=10.0),
    lambda e: e['adjustments'][0].update(kind='invented'),
    lambda e: e['adjustments'][0].update(line_index=True),
    lambda e: e.update(extra='unexpected'),
])
def test_rejected_accounting_corrections_do_not_mutate_the_payload(mutate):
    raw = draft(); raw['accounting_evidence'] = deepcopy(EVIDENCE)
    mutate(raw['accounting_evidence']); before = deepcopy(raw)
    with pytest.raises(ReviewError) as error:parse_review_draft(raw)
    assert error.value.code == 'invalid_correction' and error.value.status == 422
    assert raw == before
