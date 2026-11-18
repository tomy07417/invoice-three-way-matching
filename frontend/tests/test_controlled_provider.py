"""Fixture contract only; actual durable/UI proof belongs to manager-browser.mjs.

No Azure, database, application-data reset or real documents are used here.
"""
import asyncio
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.ports.invoice_documents import ExtractionError

spec = spec_from_file_location('controlled_browser_fixture', Path(__file__).with_name('intake-backend.py'))
fixture = module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_controlled_rate_limit_uses_retryable_provider_contract():
    extractor = fixture.ControlledExtractor(review=True)
    document = SimpleNamespace(metadata=SimpleNamespace(file_name='rate-limited.png'))
    with pytest.raises(ExtractionError) as caught:
        asyncio.run(extractor.extract(document))
    assert caught.value.code == 'rate_limited'
    assert caught.value.retryable is True
    assert caught.value.retry_after == 120
    assert extractor.count == 1


@pytest.mark.parametrize(('review', 'filename', 'quantity', 'price', 'currency', 'total'), [
    (False, 'invoice.png', '6', '10.000000', 'USD', '60.000000'),
    (True, 'invoice.png', '2', '12.000000', 'USD', '24.000000'),
    (True, 'manager.png', '1', '5001.000000', 'USD', '5001.000000'),
    (True, 'eur.png', '1', '1.000000', 'EUR', '1.000000'),
])
def test_existing_controlled_examples_are_unchanged(review, filename, quantity, price, currency, total):
    extractor = fixture.ControlledExtractor(review=review)
    result = asyncio.run(extractor.extract(SimpleNamespace(metadata=SimpleNamespace(file_name=filename))))
    assert result.is_invoice is True
    assert result.invoice.invoice_number == 'BROWSER-1'
    assert result.invoice.currency == currency
    assert result.invoice.total_amount == Decimal(total)
    assert result.invoice.subtotal == Decimal(total)
    assert result.invoice.tax_amount == Decimal('0.000000')
    assert result.invoice.lines[0].quantity == Decimal(quantity)
    assert result.invoice.lines[0].unit_price == Decimal(price)
    assert result.invoice.lines[0].line_total == Decimal(total)
    assert result.invoice.po_references == ('PO-BROWSER',)
    assert result.warnings == ('Controlled extraction warning.',)
    assert result.usage.input_tokens == result.usage.output_tokens == 10
    assert extractor.count == 1
