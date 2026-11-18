import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const { api } = await loadTypeScript('../lib/api.ts');
const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
const { safeReturnPath } = await loadTypeScript('../lib/intake-state.ts');
const id = '11111111-2222-4333-8444-555555555555';
const settings = { version_id: 1, price_tolerance: '0.0200', analyst_approval_limit: '5000.0000', currency: 'USD',
  effective_from: '2026-11-18T10:00:00+00:00', actor: null, actor_reference: 'bootstrap' };
const changes = { items: [{ ...settings, previous_version_id: null, before: null,
  after: { price_tolerance: settings.price_tolerance, analyst_approval_limit: settings.analyst_approval_limit },
  comment: null, provenance: 'bootstrap' }], total: 1, offset: 0, limit: 20 };

test('Manager and audit returns stay within exact local capability routes', () => {
  for (const path of ['/manager', '/manager/parameters', '/manager/escalations', '/audit', `/audit/${id}`]) {
    assert.equal(safeReturnPath(path), path);
  }
  for (const path of ['/manager.evil', '/manager/../login', '/audit/%2f%2fattacker', '/audit/invalid', '//evil/manager']) {
    assert.equal(safeReturnPath(path), '/invoices');
  }
});

test('Manager transport preserves exact fields, history, optional comment and quoted CAS', async () => {
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return Response.json(String(url).includes('/history') ? changes : settings);
  };
  assert.deepEqual(await api.parameters(), settings);
  assert.deepEqual(await api.parameterHistory({ offset: 0, limit: 20 }), changes);
  const update = { price_tolerance: '1.123456789012345678901', analyst_approval_limit: '9007199254740993.0000' };
  await api.updateParameters(1, update, 'csrf');
  assert.equal(requests[2].url, '/api/backend/manager/parameters');
  assert.equal(requests[2].init.method, 'PATCH');
  assert.deepEqual(JSON.parse(requests[2].init.body), update);
  assert.equal(requests[2].init.headers['If-Match'], '"1"');
  assert.equal(requests[2].init.headers['X-CSRF-Token'], 'csrf');
  assert.equal(requests[2].init.cache, 'no-store');
  await assert.rejects(async () => api.updateParameters(1.5, update, 'csrf'), /version/i);
  assert.equal(requests.length, 3);
});

test('Audit transport uses only private UUID routes and bounded filter names, preserving unknown metadata', async () => {
  let request;
  const audit = { invoice_id: id, revision: 0, events: [], total: 0, offset: 0, limit: 50 };
  globalThis.fetch = async (url, init) => { request = { url, init }; return Response.json(audit); };
  const controller = new AbortController();
  assert.deepEqual(await api.auditHistory(id, { kind: 'correction', actor_id: '42', time_from: '2026-11-18T10:00:00Z' }, controller.signal), audit);
  const url = new URL(request.url, 'http://localhost');
  assert.equal(url.pathname, '/api/backend/audit/' + id);
  assert.equal(url.searchParams.get('kind'), 'correction');
  assert.equal(url.searchParams.get('actor_id'), '42');
  assert.equal(request.init.signal, controller.signal);
  assert.equal(request.init.credentials, 'same-origin');
  await assert.rejects(async () => api.auditHistory('../manager'), /invoice ID/i);
});

test('Manager and audit success payloads reject wrong structural contracts, not just nonobjects', async () => {
  for (const invalid of [{}, { ...settings, analyst_approval_limit: 5000 }, { ...settings, version_id: '1' }]) {
    globalThis.fetch = async () => Response.json(invalid);
    await assert.rejects(() => api.parameters(), error => error.code === 'invalid_response');
  }
  globalThis.fetch = async () => Response.json({ invoice_id: id, revision: 0, events: 'not-events', total: 0, offset: 0, limit: 50 });
  await assert.rejects(() => api.auditHistory(id), error => error.code === 'invalid_response');
});

test('Proxy narrowly admits Manager/audit, forwards version headers and rejects sibling widening', async () => {
  process.env.BACKEND_INTERNAL_URL = 'http://127.0.0.1:3211';
  process.env.FRONTEND_ORIGIN = 'http://localhost:3000';
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return Response.json(settings, { headers: { ETag: '"1"' } });
  };
  const patch = await proxyBackend(new Request('http://localhost:3000/api/backend/manager/parameters', {
    method: 'PATCH', headers: { Origin: 'http://localhost:3000', 'If-Match': '"1"', 'X-CSRF-Token': 'csrf', 'Content-Type': 'application/json' },
    body: JSON.stringify({ price_tolerance: '0.03' }),
  }));
  assert.equal(patch.status, 200); assert.equal(patch.headers.get('etag'), '"1"');
  assert.equal(requests[0].init.headers.get('if-match'), '"1"');
  assert.equal(requests[0].init.headers.get('x-csrf-token'), 'csrf');
  assert.equal(new TextDecoder().decode(requests[0].init.body), '{"price_tolerance":"0.03"}');
  for (const path of ['/manager/parameters/history?offset=0&limit=20', `/audit/${id}?kind=correction&limit=20`]) {
    assert.equal((await proxyBackend(new Request('http://localhost:3000/api/backend' + path))).status, 200);
  }
  const count = requests.length;
  for (const [path, status] of [['/manager/users', 404], ['/manager/parameters?offset=1', 400],
    ['/manager/parameters/history?kind=correction', 400], [`/audit/${id}?kind=correction&kind=decision`, 400],
    ['/invoices?kind=correction', 400]]) {
    assert.equal((await proxyBackend(new Request('http://localhost:3000/api/backend' + path))).status, status);
  }
  assert.equal(requests.length, count);
});
