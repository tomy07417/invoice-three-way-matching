import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const id = '11111111-2222-4333-8444-555555555555';

test('review reads retain exact values, filters, same-origin credentials and aborts', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  const requests = [];
  const signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    requests.push(url);
    assert.equal(init.signal, signal);
    assert.equal(init.credentials, 'same-origin');
    assert.equal(init.cache, 'no-store');
    return Response.json({ total_amount: '9007199254740993.1200' });
  });
  const result = await api.reviewList({ supplier: 'A & B', min_amount: '1.0100', outcome: 'all', offset: 20 }, signal);
  assert.equal(result.total_amount, '9007199254740993.1200');
  await api.reviewDetail(id, signal);
  await api.reviewHistory(id, signal);
  assert.deepEqual(requests, ['/api/backend/review?supplier=A+%26+B&min_amount=1.0100&outcome=all&offset=20',
    `/api/backend/review/${id}`, `/api/backend/review/${id}/history`]);
});

test('review writes bind full exact draft, comment, CSRF and quoted expected revision', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => { calls.push([url, init]); return Response.json({ revision: 8 }); });
  const invoice = { total_amount: '24.0000', lines: [{ quantity: '2.00' }] };
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
