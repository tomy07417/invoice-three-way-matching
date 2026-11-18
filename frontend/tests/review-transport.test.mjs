import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const id = '11111111-2222-4333-8444-555555555555';
const at = '2026-11-18T10:00:00Z';
const detail = { invoice_id: id, revision: 8, extraction_version: 1, extraction: null, original_extraction: null,
  evaluation: null, comparison_rows: [], operational_state: 'REVIEW_REQUIRED', payment_blocked: true,
  credit_note_pending: false, requires_reevaluation: false, eligibility: { allowed: false, requires_manager: false,
    reason: null, limit_amount: '5000', limit_currency: 'USD' }, current_decision: null,
  document_url: `/invoices/${id}/document`, created_at: at, updated_at: at };
const summary = { invoice_id: id, revision: 8, invoice_number: null, supplier_name: null, invoice_date: null,
  total_amount: '9007199254740993.1200', currency: null, operational_state: 'REVIEW_REQUIRED', payment_blocked: true,
  automatic_outcome: null, reasons: [], requires_manager: false, created_at: at, age_seconds: 0 };

test('review reads retain exact values, filters, same-origin credentials and aborts', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  const requests = [];
  const signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    requests.push(url);
    assert.equal(init.signal, signal);
    assert.equal(init.credentials, 'same-origin');
    assert.equal(init.cache, 'no-store');
    return Response.json(url.includes('?') ? { items: [summary], total: 1, offset: 20, limit: 20 }
      : url.endsWith('/history') ? { invoice_id: id, revision: 8, events: [] } : detail);
  });
  const result = await api.reviewList({ supplier: 'A & B', min_amount: '1.0100', outcome: 'all', offset: 20 }, signal);
  assert.equal(result.items[0].total_amount, '9007199254740993.1200');
  await api.reviewDetail(id, signal);
  await api.reviewHistory(id, signal);
  assert.deepEqual(requests, ['/api/backend/review?supplier=A+%26+B&min_amount=1.0100&outcome=all&offset=20',
    `/api/backend/review/${id}`, `/api/backend/review/${id}/history`]);
});

test('review writes bind full exact draft, comment, CSRF and quoted expected revision', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => { calls.push([url, init]); return Response.json(detail); });
  const invoice = { supplier_name: null, supplier_tax_id: null, invoice_number: null, invoice_date: null,
    due_date: null, po_references: null, currency: null, subtotal: null, tax_amount: null, total_amount: '24.0000',
    lines: [{ description: null, quantity: '2.00', unit_price: null, line_total: null, po_line_reference: null,
      po_number: null, po_line_number: null, unit_price_basis: 'unknown' }] };
  await api.correctExtraction(id, 7, invoice, 'Verified original', 'csrf');
  await api.reevaluate(id, 8, 'Refresh evidence', 'csrf');
  await api.decide(id, 9, 'request_credit_note', 'Tax discrepancy', 'csrf');
  await api.escalate(id, 10, 'Manager required', 'csrf');
  assert.deepEqual(calls.map(([url, init]) => [url, init.method]), [
    [`/api/backend/review/${id}`, 'PATCH'], [`/api/backend/review/${id}/reevaluations`, 'POST'],
    [`/api/backend/review/${id}/decisions`, 'POST'], [`/api/backend/review/${id}/escalations`, 'POST'],
  ]);
  for (const [, init] of calls) {
    assert.equal(new Headers(init.headers).get('X-CSRF-Token'), 'csrf');
    assert.equal(new Headers(init.headers).get('Content-Type'), 'application/json');
    assert.equal(new Headers(init.headers).get('cookie'), null);
  }
  assert.equal(new Headers(calls[0][1].headers).get('If-Match'), '"7"');
  assert.deepEqual(JSON.parse(calls[0][1].body), { invoice, comment: 'Verified original' });
  assert.deepEqual(JSON.parse(calls[2][1].body), { action: 'request_credit_note', comment: 'Tax discrepancy' });
  assert.throws(() => api.decide(id, -1, 'approve', 'Reason', 'csrf'), /revision/i);
});

test('proxy allows exact review reads/query and private versioned PATCH without widening Intake', async t => {
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push([url, init]);
    return Response.json({ revision: 1 }, { headers: { ETag: '"1"' } });
  });
  const base = 'http://localhost:3000/api/backend';
  const response = await proxyBackend(new Request(base + `/review/${id}`, { method: 'PATCH',
    headers: { Origin: 'http://localhost:3000', 'X-CSRF-Token': 'csrf', 'If-Match': '"0"', 'Content-Type': 'application/json' },
    body: '{"invoice":{"total_amount":"24.00"},"comment":"Reason"}' }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('etag'), '"1"');
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(calls[0][1].headers.get('if-match'), '"0"');
  assert.equal(JSON.parse(Buffer.from(calls[0][1].body).toString()).invoice.total_amount, '24.00');
  for (const suffix of ['', `/${id}`, `/${id}/history`]) assert.equal((await proxyBackend(new Request(base + '/review' + suffix))).status, 200);
  for (const suffix of ['reevaluations', 'decisions', 'escalations']) {
    assert.equal((await proxyBackend(new Request(base + `/review/${id}/${suffix}`, {
      method: 'POST', headers: { Origin: 'http://localhost:3000', 'If-Match': '"1"' }, body: '{}',
    }))).status, 200);
  }
  assert.equal((await proxyBackend(new Request(base + '/review?supplier=A&date_from=2026-10-01&min_amount=1.00&reason=price&operational_state=ESCALATED&sort=amount&direction=desc&min_age_days=1&outcome=all&offset=0&limit=20'))).status, 200);
  const count = calls.length;
  for (const path of ['/review?status=COMPLETED', '/review?limit=1&limit=2', `/review/${id}/admin`, '/invoices?supplier=A']) {
    assert.ok((await proxyBackend(new Request(base + path))).status >= 400);
  }
  assert.equal((await proxyBackend(new Request(base + `/invoices/${id}`, { method: 'PATCH', headers: { Origin: 'http://localhost:3000' }, body: '{}' }))).status, 405);
  assert.equal((await proxyBackend(new Request(base + `/review/${id}`, { method: 'PATCH', headers: { Origin: 'https://attacker.test' }, body: '{}' }))).status, 403);
  assert.equal(calls.length, count);
});
