from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.application.dtos.auth import Actor, Role
from app.application.dtos.invoice_documents import DocumentMetadata, ExtractionResult, InvoiceDraft, TokenUsage
from app.application.dtos.processing import ProcessingConfig, ProcessingState
from app.application.ports.processing import AdmissionRejected, StaleLease
from app.infrastructure.persistence.auth_repository import SqlAuthRepository
from app.infrastructure.persistence.processing_repository import SqlProcessingRepository


@pytest.fixture
def setup(migrated_engine):
    auth = SqlAuthRepository(migrated_engine)
    # Reuse the account creation port; no processing-specific user insertion.
    from app.application.use_cases.sessions import SessionService
    service = SessionService(auth)
    actor = service.provision('owner', 'long enough password', Role.INTAKE)
    other = service.provision('other', 'long enough password', Role.INTAKE)
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    repo = SqlProcessingRepository(migrated_engine, clock=lambda: now[0], random=lambda: .5)
    metadata = DocumentMetadata('invoice.pdf', 'application/pdf', 100, 'a' * 64, 1)
    return repo, actor, other, now, metadata


def admit(setup, key='one', estimate=6000):
    repo, actor, _, _, metadata = setup
    return repo.admit(actor, key, 'opaque-storage-key', metadata, estimate)


def test_admission_idempotency_owner_and_rejection_preserves(setup):
    repo, actor, other, _, metadata = setup
    first = admit(setup)
    assert repo.admit(actor, 'one', 'different', metadata, 6000) == first
    assert repo.get(other, first.invoice_id) is None
    assert repo.list(other) == ()
    manager = Actor(other.actor_id, other.username, Role.AP_MANAGER)
    assert repo.get(manager, first.invoice_id) == first
    for n in range(1, 20):
        admit(setup, str(n))
    before = repo.list(actor)
    with pytest.raises(AdmissionRejected):
        admit(setup, 'overflow')
    with pytest.raises(ValueError):
        admit(setup, 'invalid', 10001)
    assert repo.list(actor) == before
    assert admit(setup) == first


def test_concurrent_claim_budget_persisted_wait_and_no_early_reclaim(setup):
    repo, actor, _, now, _ = setup
    first = admit(setup)
    now[0] += timedelta(microseconds=1)
    second = admit(setup, 'two')
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: repo.claim(), range(4)))
    claim, = [c for c in claims if c is not None]
    assert claim.job.invoice_id == first.invoice_id
    repo.extracted(claim, ExtractionResult(False, None, (), TokenUsage(2000, 1000)))
    # Conservative settlement retains the estimate, not the smaller report.
    assert repo.claim() is None
    waiting = repo.get(actor, second.invoice_id)
    assert waiting.state == ProcessingState.RETRY_WAIT
    assert waiting.attempts == 0
    assert waiting.next_eligible_at > now[0] + timedelta(seconds=60)
    assert repo.claim() is None
    now[0] = waiting.next_eligible_at
    assert repo.claim().job.invoice_id == second.invoice_id


def test_crash_restart_fence_finite_attempts_and_history(setup, migrated_engine):
    repo, actor, _, now, _ = setup
    job = admit(setup, estimate=1000)
    old = repo.claim()
    for expected in (2, 3):
        now[0] += timedelta(seconds=200)
        repo = SqlProcessingRepository(migrated_engine, clock=lambda: now[0], random=lambda: .5)
        # Lease recovery schedules a persisted retry, not a same-tick call.
        assert repo.claim() is None
        wait = repo.get(actor, job.invoice_id)
        now[0] = wait.next_eligible_at
        current = repo.claim()
        assert current.job.attempts == expected
    with pytest.raises(StaleLease):
        repo.extracted(old, ExtractionResult(False, None, ()))
    now[0] += timedelta(seconds=200)
    assert repo.claim() is None
    final = repo.get(actor, job.invoice_id)
    assert final.state == ProcessingState.EXTRACTION_FAILED
    assert final.attempts == 3
    assert len(final.attempt_history) == 3
    assert all(a['reserved_tokens'] == 1000 for a in final.attempt_history)
    assert repo.claim() is None


def test_error_floor_terminal_and_exact_draft_with_atomic_hook(setup):
    repo, actor, _, now, _ = setup
    job = admit(setup, estimate=1000)
    claim = repo.claim()
    floor = now[0] + timedelta(seconds=300)
    repo.failed(claim, 'timeout', retryable=True, retry_after=floor)
    wait = repo.get(actor, job.invoice_id)
    assert wait.next_eligible_at > floor
    assert repo.claim() is None
    now[0] = wait.next_eligible_at
    claim = repo.claim()
    draft = InvoiceDraft('supplier', None, '123', date(2026, 1, 1), None, (), 'USD', Decimal('1.2300'), None, None, ())
    result = ExtractionResult(True, draft, (), TokenUsage(10, 20))
    repo.extracted(claim, result)
    stored = repo.get(actor, job.invoice_id)
    assert stored.extraction == result
    assert str(stored.extraction.invoice.subtotal) == '1.2300'
    evaluation = repo.claim_evaluation()
    def abort(tx, case):
        assert hasattr(tx, 'append_evaluation')
        raise RuntimeError('rollback')
    with pytest.raises(RuntimeError):
        repo.complete(evaluation, abort)
    assert repo.get(actor, job.invoice_id).state == ProcessingState.EVALUATING
    # Integration callback returns (raw_invoice_id, evaluation_id, complete).
    repo.complete(evaluation, lambda tx, case: (None, None, False))
    assert repo.get(actor, job.invoice_id).state == ProcessingState.EVALUATION_INCOMPLETE
    terminal = admit(setup, 'terminal', estimate=1000)
    claim = repo.claim()
    repo.failed(claim, 'unsupported_document', retryable=False)
    assert repo.get(actor, terminal.invoice_id).state == ProcessingState.EXTRACTION_FAILED
    assert repo.claim() is None


def test_concurrent_admission_and_failed_reservations_are_not_free(setup):
    repo, actor, _, now, metadata = setup
    def submit(n):
        try:
            return repo.admit(actor, str(n), 'private-key', metadata, 6000)
        except AdmissionRejected:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(24)))
    assert sum(result is not None for result in results) == 20
    assert len(repo.list(actor)) == 20
    claim = repo.claim()
    repo.failed(claim, 'provider_unavailable', retryable=False)
    assert repo.claim() is None
    waiting = [j for j in repo.list(actor) if j.state == ProcessingState.RETRY_WAIT]
    assert len(waiting) == 1
    assert waiting[0].attempts == 0
    assert waiting[0].next_eligible_at > now[0] + timedelta(seconds=60)
    # Terminal errors are allowlisted; arbitrary error content is never stored.
    now[0] += timedelta(seconds=100)
    claim = repo.claim()
    repo.failed(claim, 'sensitive provider payload', retryable=False)
    assert repo.get(actor, claim.job.invoice_id).error_code == 'processing_failed'


def test_global_tokens_across_independent_repositories_and_usage_overrun(setup, migrated_engine):
    from app.application.use_cases.retry_policy import RetryPolicy
    _, actor, _, now, metadata = setup
    config = ProcessingConfig(max_active=2)
    repo = SqlProcessingRepository(migrated_engine, config=config, clock=lambda: now[0], random=lambda: .25)
    other = SqlProcessingRepository(migrated_engine, config=config, clock=lambda: now[0], random=lambda: .75)
    first = repo.admit(actor, 'a', 'storage-a', metadata, 3000)
    now[0] += timedelta(microseconds=1)
    second = repo.admit(actor, 'b', 'storage-b', metadata, 3000)
    now[0] += timedelta(microseconds=1)
    third = repo.admit(actor, 'c', 'storage-c', metadata, 5000)
    a, b = repo.claim(), other.claim()
    assert {a.job.invoice_id, b.job.invoice_id} == {first.invoice_id, second.invoice_id}
    assert repo.claim() is None
    repo.extracted(a, ExtractionResult(False, None, (), TokenUsage(6000, 2000)))
    assert other.claim() is None
    waiting = repo.get(actor, third.invoice_id)
    assert waiting.state == ProcessingState.RETRY_WAIT
    assert waiting.attempts == 0
    # Config drift must not bypass global coordination or mutate jobs.
    before = repo.list(actor)
    mismatch = SqlProcessingRepository(migrated_engine, config=config, retry_policy=RetryPolicy(max_attempts=4))
    with pytest.raises(ValueError, match='Workers must share'):
        mismatch.claim()
    assert repo.list(actor) == before


def test_fenced_completion_commits_or_rolls_back_actual_evaluation(setup, migrated_engine):
    from app.application.dtos.invoice_evaluation import EvaluationRecord
    from app.domain.entities import Invoice
    from app.domain.matching.types import EvaluationResult, RuleResult
    from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository
    repo, actor, _, now, _ = setup
    job = admit(setup, estimate=1000)
    claim = repo.claim()
    repo.extracted(claim, ExtractionResult(False, None, ()))
    evaluation_claim = repo.claim_evaluation()
    created = []
    def append(tx, case):
        raw = Invoice('supplier', 'tax', 'number', date(2026, 1, 1), None, (), 'USD', Decimal('1'), Decimal('0'), Decimal('1'), ())
        raw_id = tx.register_invoice(raw, None)
        configuration = tx.get_configuration(now[0])
        result = EvaluationResult('Rejected', 'Rejected', (RuleResult('no_po', 'REJECT', 'No PO'),), configuration.config)
        record = tx.append_evaluation(EvaluationRecord(raw_id, configuration.configuration_id, now[0], str(actor.actor_id), 'processing', result, {}, {}))
        created.append((raw_id, record.evaluation_id))
        return raw_id, record.evaluation_id, True
    def abort(tx, case):
        append(tx, case)
        raise RuntimeError('rollback')
    with pytest.raises(RuntimeError):
        repo.complete(evaluation_claim, abort)
    with SqlAlchemyInvoiceEvaluationRepository(migrated_engine).transaction() as tx:
        assert tx.get_invoice(created[0][0]) is None
        assert tx.get_evaluation(created[0][1]) is None
    repo.complete(evaluation_claim, append)
    stored = repo.get(actor, job.invoice_id)
    assert stored.state == ProcessingState.COMPLETED
    assert (stored.raw_invoice_id, stored.evaluation_id) == created[-1]
    with pytest.raises(StaleLease):
        repo.complete(evaluation_claim, append)
    with SqlAlchemyInvoiceEvaluationRepository(migrated_engine).transaction() as tx:
        assert len(tx.list_evaluations(stored.raw_invoice_id)) == 1
        assert tx.get_approved_quantities(()) == {}


@pytest.mark.parametrize('kwargs', [dict(max_active=0), dict(max_pending=0), dict(token_limit=10001), dict(window_seconds=0), dict(lease_seconds=0)])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        ProcessingConfig(**kwargs)
