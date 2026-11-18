import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';

const id = '11111111-2222-4333-8444-555555555555';
const at = '2026-11-18T10:00:00+00:00';
const session = { actor: { actor_id: 1, username: 'demo', role: 'intake' }, csrf_token: 'csrf', expires_at: at };
const snapshot = { invoice_id: id, job_id: id, processing_status: 'QUEUED', attempts: 0,
  next_eligible_at: at, created_at: at, updated_at: at, automatic_outcome: null, error: null };
const line = { description: null, quantity: '-1.00', unit_price: '1E+30', line_total: null,
  po_line_reference: null, po_number: null, po_line_number: null, unit_price_basis: 'unknown' };
const draft = { supplier_name: null, supplier_tax_id: null, invoice_number: null, invoice_date: null,
  due_date: null, po_references: null, currency: null, subtotal: null, tax_amount: null,
  total_amount: '9007199254740993.1200', lines: [line] };
const finding = { code: 'missing_evidence', outcome: 'PENDING', reason: 'Required facts are missing',
  po_number: null, line_index: null, po_line_number: null };
const evaluation = { invoice_id: 1, configuration_id: 1, evaluated_at: at, actor_id: '1', origin: 'upload',
  evaluation_id: 1, result: { provisional_status: 'Held for Review', final_status: null, rules: [finding],
    config: { price_tolerance: '0.02', analyst_approval_limit: '5000' } }, input_snapshot: {}, context_snapshot: {} };
const detail = { ...snapshot, processing_status: 'EVALUATION_INCOMPLETE', document: { file_name: 'invoice.pdf',
  media_type: 'application/pdf', byte_size: 3, sha256: 'a'.repeat(64), page_count: 1, pixel_width: null, pixel_height: null },
  extraction: { is_invoice: true, invoice: draft, warnings: [] }, evaluation, findings: [finding] };
const summary = { ...snapshot, invoice_number: null, supplier_name: null, invoice_date: null,
  total_amount: draft.total_amount, currency: null };
const processing = { ...snapshot, attempt_history: [{ number: 1, started_at: at, finished_at: null,
  reserved_tokens: 6000, reported_tokens: null, error_code: null }], events: [{ at, state: 'QUEUED', error_code: null }] };
const comparison = { line_index: 0, description: null, quantity_invoiced: '-1E-3', po_number: null,
  po_line_number: null, quantity_ordered: null, quantity_received: null, quantity_previously_approved: null,
  quantity_available: '-2.00', po_net_unit_price: null, invoice_net_unit_price: null,
  invoice_currency: null, po_currency: null, tolerance: null, association_status: 'unresolved',
  findings: [{ rule: finding.code, message: finding.reason,
    evidence: { outcome: 'PENDING', po_number: null, line_index: null, po_line_number: null } }] };
const review = { invoice_id: id, revision: 0, extraction_version: 1, extraction: draft, original_extraction: null,
  evaluation, comparison_rows: [comparison], operational_state: 'REVIEW_REQUIRED', payment_blocked: true,
  credit_note_pending: false, requires_reevaluation: false, eligibility: { allowed: false, requires_manager: false,
    reason: null, limit_amount: '5000.0000', limit_currency: 'USD' }, current_decision: null,
  document_url: `/invoices/${id}/document`, created_at: at, updated_at: at };
const reviewSummary = { invoice_id: id, revision: 0, invoice_number: null, supplier_name: null, invoice_date: null,
  total_amount: draft.total_amount, currency: null, operational_state: 'REVIEW_REQUIRED', payment_blocked: true,
  automatic_outcome: null, reasons: [finding.reason], requires_manager: false, created_at: at, age_seconds: 0 };
const history = { invoice_id: id, revision: 0, events: [{ kind: 'evaluation', at, actor_id: '1',
  comment: 'Original automatic evaluation', current: true, extraction_version: 1, evaluation_id: 1, evaluation }] };
const page = item => ({ items: [item], total: 1, offset: 0, limit: 20 });
const clone = value => structuredClone(value);
const reads = [
  ['currentSession', session, api => api.currentSession()], ['login', session, api => api.login('demo', 'password')],
  ['list', page(summary), api => api.list()], ['detail', detail, api => api.detail(id)],
  ['processing', processing, api => api.processing(id)], ['retry', snapshot, api => api.retry(id, 'csrf')],
  ['reviewList', page(reviewSummary), api => api.reviewList()], ['reviewDetail', review, api => api.reviewDetail(id)],
  ['reviewHistory', history, api => api.reviewHistory(id)],
  ['correctExtraction', review, api => api.correctExtraction(id, 0, draft, 'Reason', 'csrf')],
  ['reevaluate', review, api => api.reevaluate(id, 0, 'Reason', 'csrf')],
  ['decide', review, api => api.decide(id, 0, 'reject', 'Reason', 'csrf')],
  ['escalate', review, api => api.escalate(id, 0, 'Reason', 'csrf')],
];
for (const [name, valid, call] of reads) {
  test(`${name} preserves the complete public DTO and exact nullable evidence`, async t => {
    const { api } = await loadTypeScript('../lib/api.ts');
    t.mock.method(globalThis, 'fetch', async () => Response.json(valid));
    assert.deepEqual(await call(api), valid);
  });
}
test('AP responses preserve additive association provenance without breaking verified-link status', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  const value = clone(review);
  value.comparison_rows[0].association_status = 'explicit';
  value.comparison_rows[0].association_method = 'description';
  t.mock.method(globalThis, 'fetch', async () => Response.json(value));
  for (const call of [() => api.reviewDetail(id), () => api.reevaluate(id, 0, 'Reason', 'csrf'),
    () => api.decide(id, 0, 'approve', 'Reason', 'csrf')]) {
    assert.deepEqual(await call(), value);
  }
});
const invalidCases = [
  ['currentSession', 'unknown role', v => { v.actor.role = 'admin'; }],
  ['login', 'nested actor type', v => { v.actor.actor_id = '1'; }],
  ['currentSession', 'missing CSRF', v => { delete v.csrf_token; }],
  ['list', 'JSON number Decimal', v => { v.items[0].total_amount = 123; }],
  ['list', 'nested processing state', v => { v.items[0].processing_status = 'APPROVED'; }],
  ['detail', 'nested line Decimal', v => { v.extraction.invoice.lines[0].quantity = 1; }],
  ['detail', 'nonfinite Decimal string', v => { v.extraction.invoice.total_amount = 'NaN'; }],
  ['detail', 'document metadata', v => { v.document.pixel_width = '100'; }],
  ['detail', 'extraction warnings', v => { v.extraction.warnings = [1]; }],
  ['detail', 'finding outcome', v => { v.findings[0].outcome = 'APPROVED'; }],
  ['detail', 'evaluation config Decimal', v => { v.evaluation.result.config.price_tolerance = 0.02; }],
  ['detail', 'final versus provisional outcome', v => { v.evaluation.result.final_status = 'PENDING'; }],
  ['detail', 'snapshot object', v => { v.evaluation.input_snapshot = []; }],
  ['processing', 'attempt fields', v => { v.attempt_history[0].reported_tokens = '6000'; }],
  ['processing', 'event state', v => { v.events[0].state = 'Auto-Pay'; }],
  ['retry', 'error retryability', v => { v.error = { code: 'timeout', message: 'Retry', retryable: 'yes' }; }],
  ['reviewList', 'missing version', v => { delete v.items[0].revision; }],
  ['reviewList', 'reasons', v => { v.items[0].reasons = [null]; }],
  ['reviewDetail', 'missing extraction version', v => { delete v.extraction_version; }],
  ['reviewDetail', 'eligibility Decimal', v => { v.eligibility.limit_amount = 5000; }],
  ['reviewDetail', 'comparison Decimal', v => { v.comparison_rows[0].quantity_available = 0; }],
  ['reviewDetail', 'comparison finding evidence', v => { v.comparison_rows[0].findings[0].evidence.outcome = 1; }],
  ['reviewDetail', 'human decision', v => { v.current_decision = { action: 'approve' }; }],
  ['reviewDetail', 'operational state', v => { v.operational_state = 'COMPLETED'; }],
  ['reviewHistory', 'event evaluation', v => { v.events[0].evaluation.result.rules[0].reason = null; }],
  ['reviewHistory', 'event invoice', v => { v.events[0].invoice = { lines: [] }; }],
  ['reviewHistory', 'event version', v => { v.events[0].extraction_version = '1'; }],
  ...['correctExtraction', 'reevaluate', 'decide', 'escalate'].map(name => [name, 'mutation detail version', v => { delete v.revision; }]),
];
for (const [name, rule, mutate] of invalidCases) {
  test(`${name} rejects ${rule} with sanitized correlated invalid_response`, async t => {
    const { api, ApiError } = await loadTypeScript('../lib/api.ts');
    const [, valid, call] = reads.find(([method]) => method === name);
    const invalid = clone(valid); mutate(invalid);
    t.mock.method(globalThis, 'fetch', async () => Response.json(invalid, { headers: { 'X-Correlation-ID': 'trace-safe' } }));
    await assert.rejects(call(api), error => {
      assert.ok(error instanceof ApiError); assert.equal(error.status, 200);
      assert.equal(error.code, 'invalid_response'); assert.equal(error.message, 'Unexpected service response');
      assert.equal(error.correlationId, 'trace-safe'); assert.equal(error.retryable, true); return true;
    });
  });
}
test('terminal, incomplete and pending states retain separate automatic and human decisions', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  for (const state of ['QUEUED', 'RETRY_WAIT', 'COMPLETED', 'EVALUATION_INCOMPLETE', 'EXTRACTION_FAILED', 'PROCESSING_FAILED']) {
    const value = { ...detail, processing_status: state, automatic_outcome: state === 'COMPLETED' ? 'Rejected' : null };
    t.mock.method(globalThis, 'fetch', async () => Response.json(value));
    assert.deepEqual(await api.detail(id), value);
  }
  for (const value of ['-9007199254740993.1200', '1E+1000', '-1e-1000', '+.5', '0.0000']) {
    const valid = clone(review); valid.extraction.total_amount = value;
    t.mock.method(globalThis, 'fetch', async () => Response.json(valid));
    assert.equal((await api.reviewDetail(id)).extraction.total_amount, value);
  }
});
test('all actual roles, unknown extraction and recorded human decisions remain valid', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  for (const role of ['intake', 'ap_analyst', 'ap_manager']) {
    const value = { ...session, actor: { ...session.actor, role } };
    t.mock.method(globalThis, 'fetch', async () => Response.json(value));
    assert.deepEqual(await api.currentSession(), value);
  }
  const unknown = { ...detail, extraction: { is_invoice: false, invoice: null, warnings: ['Not an invoice'] }, evaluation: null };
  t.mock.method(globalThis, 'fetch', async () => Response.json(unknown));
  assert.deepEqual(await api.detail(id), unknown);
  const current = { id: 1, revision: 1, extraction_version: 1, evaluation_id: null,
    action: 'request_credit_note', actor_id: '1', at, comment: 'Verified discrepancy' };
  const value = { ...review, revision: 1, operational_state: 'CREDIT_NOTE_REQUESTED', credit_note_pending: true,
    current_decision: current };
  t.mock.method(globalThis, 'fetch', async () => Response.json(value));
  assert.deepEqual(await api.reviewDetail(id), value);
  const events = { ...history, revision: 1, events: [
    { kind: 'extraction', at, actor_id: '1', current: false, comment: 'Original', extraction_version: 1, invoice: null },
    { kind: 'decision', ...current, decision_id: 1, current: true, actor_name: 'Analyst', actor_role: 'ap_analyst' },
    { kind: 'extraction', at, actor_id: '1', current: true, comment: 'Corrected', extraction_version: 2, invoice: draft },
  ] };
  t.mock.method(globalThis, 'fetch', async () => Response.json(events));
  assert.deepEqual(await api.reviewHistory(id), events);
});
test('unexpected JSON detail is sanitized but real session public failures survive', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  for (const [status, detail, expected] of [[503, 'Traceback secret=key', 'Service temporarily unavailable'],
    [401, 'secret=key', 'Authentication required'], [401, 'Invalid username or password', 'Invalid username or password'],
    [403, 'CSRF validation failed', 'CSRF validation failed'], [503, 'Account service unavailable', 'Account service unavailable']]) {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ detail }, { status, headers: { 'X-Correlation-ID': 'trace' } }));
    await assert.rejects(api.currentSession(), { message: expected, correlationId: 'trace' });
  }
});
test('XHR upload validates the same complete processing snapshot without changing its stable key', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  let value = snapshot; const keys = [];
  class XHR {
    upload = {}; status = 202;
    open() {} setRequestHeader(name, value) { if (name === 'Idempotency-Key') keys.push(value); }
    getResponseHeader() { return 'upload-trace'; }
    send() { this.responseText = JSON.stringify(value); this.onload(); }
  }
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'XMLHttpRequest');
  Object.defineProperty(globalThis, 'XMLHttpRequest', { value: XHR, configurable: true });
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, 'XMLHttpRequest', descriptor);
    else delete globalThis.XMLHttpRequest;
  });
  const file = new File(['pdf'], 'invoice.pdf', { type: 'application/pdf' });
  const options = { csrfToken: 'csrf', idempotencyKey: 'stable' };
  assert.deepEqual(await api.uploadInvoice(file, options), snapshot);
  for (const invalid of [{ ...snapshot, attempts: '0' }, { ...snapshot, invoice_id: 'case' }, { ...snapshot, error: {} }]) {
    value = invalid;
    await assert.rejects(api.uploadInvoice(file, options), { code: 'invalid_response', message: 'Unexpected service response',
      correlationId: 'upload-trace', retryable: true, status: 202 });
  }
  assert.deepEqual(keys, ['stable', 'stable', 'stable', 'stable']);
});
