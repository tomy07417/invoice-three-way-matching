import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.dtos.invoice_documents import ExtractionResult
from app.application.ports.invoice_documents import ExtractionError
from app.application.ports.processing import StaleLease
from app.application.use_cases.process_invoice import ProcessInvoice


def test_timeout_schedules_without_sleep_and_evaluation_tick():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claim = SimpleNamespace(job=SimpleNamespace(storage_key='key', metadata=None),
                            expires_at=now + timedelta(seconds=120))
    calls = []

    class Repo:
        config = SimpleNamespace(lease_seconds=120)
        def claim(self):
            return claim
        def claim_evaluation(self):
            return None
        def failed(self, c, code, **kwargs):
            calls.append((c, code, kwargs))

    class Storage:
        def read(self, key):
            return b'document'

    class Provider:
        async def extract(self, document):
            raise ExtractionError('rate_limited', 'safe', retryable=True, retry_after=30)

    # A document factory keeps this unit independent of file parsers.
    worker = ProcessInvoice(Repo(), Storage(), Provider(), clock=lambda: now,
                            document_factory=lambda data, metadata: data)
    assert asyncio.run(worker.process_once()) is True
    assert calls == [(claim, 'rate_limited', dict(retryable=True, retry_after=now + timedelta(seconds=30)))]


def test_cancellation_does_not_record_provider_failure_or_finish_lease():
    calls = []
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claim = SimpleNamespace(job=SimpleNamespace(storage_key='key', metadata=None),
                            expires_at=now + timedelta(seconds=120))

    class Repo:
        config = SimpleNamespace(lease_seconds=120)
        def claim(self):
            calls.append('claimed')
            return claim
        def failed(self, *args, **kwargs):
            calls.append('failed')
        def extracted(self, *args):
            calls.append('extracted')

    class Storage:
        def read(self, key):
            return b'document'

    class Provider:
        async def extract(self, document):
            raise asyncio.CancelledError()

    worker = ProcessInvoice(Repo(), Storage(), Provider(), clock=lambda: now,
                            document_factory=lambda data, metadata: data)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.process_once())
    assert calls == ['claimed']


@pytest.mark.parametrize('storage_seconds', [0, 40, 115, 119.5, 120, 121])
def test_provider_budget_uses_remaining_lease_after_storage(monkeypatch, storage_seconds):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    claim = SimpleNamespace(job=SimpleNamespace(storage_key='key', metadata=None),
                            expires_at=now[0] + timedelta(seconds=120))
    budgets, calls, writes = [], [], []

    @asynccontextmanager
    async def timeout(seconds):
        budgets.append(seconds)
        yield

    monkeypatch.setattr(asyncio, 'timeout', timeout)

    class Repo:
        config = SimpleNamespace(lease_seconds=120)
        def claim(self):
            return claim
        def claim_evaluation(self):
            return None
        def extracted(self, *args):
            writes.append('extracted')
        def failed(self, *args, **kwargs):
            writes.append('failed')

    class Storage:
        def read(self, key):
            now[0] += timedelta(seconds=storage_seconds)
            return b'document'

    class Provider:
        async def extract(self, document):
            calls.append(document)
            return ExtractionResult(False, None, ())

    worker = ProcessInvoice(Repo(), Storage(), Provider(), clock=lambda: now[0],
                            document_factory=lambda data, metadata: data)
    assert asyncio.run(worker.process_once())
    remaining = 120 - storage_seconds
    if remaining <= 1:
        assert calls == budgets == writes == []
    else:
        assert calls == [b'document']
        assert writes == ['extracted']
        assert budgets == [min(90, remaining - 1)]
        assert 0 < budgets[0] < remaining


def test_provider_result_still_respects_repository_fencing():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claim = SimpleNamespace(job=SimpleNamespace(storage_key='key', metadata=None),
                            expires_at=now + timedelta(seconds=120))
    writes = []

    class Repo:
        config = SimpleNamespace(lease_seconds=120)
        def claim(self):
            return claim
        def claim_evaluation(self):
            return None
        def extracted(self, received_claim, result):
            assert received_claim is claim
            writes.append('fenced')
            raise StaleLease('Claim replaced')
        def failed(self, *args, **kwargs):
            pytest.fail('A stale result must not create a new failure')

    class Storage:
        def read(self, key):
            return b'document'

    class Provider:
        async def extract(self, document):
            return ExtractionResult(False, None, ())

    worker = ProcessInvoice(Repo(), Storage(), Provider(), clock=lambda: now,
                            document_factory=lambda data, metadata: data)
    assert asyncio.run(worker.process_once())
    assert writes == ['fenced']


def test_deadline_must_be_less_than_lease():
    with pytest.raises(ValueError):
        ProcessInvoice(SimpleNamespace(config=SimpleNamespace(lease_seconds=10)), None, None, deadline=10)
