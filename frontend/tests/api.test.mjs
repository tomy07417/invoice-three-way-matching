import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';

const id = '11111111-2222-4333-8444-555555555555';
const load = () => loadTypeScript('../lib/api.ts');
function mockXHR(t, implementation) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'XMLHttpRequest');
  Object.defineProperty(globalThis, 'XMLHttpRequest', { value: implementation, configurable: true });
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, 'XMLHttpRequest', descriptor);
    else delete globalThis.XMLHttpRequest;
  });
}

test('public reads use only same-origin routes, no-store and caller AbortSignal; money stays exact', async t => {
  const { api } = await load();
  const seen = [];
  const signal = new AbortController().signal;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    seen.push(url);
    assert.equal(init.signal, signal);
    assert.equal(init.credentials, 'same-origin');
    assert.equal(init.cache, 'no-store');
    return Response.json({ total_amount: '123.4500' });
  });
  await api.currentSession(signal);
  assert.equal((await api.list({ offset: 20, limit: 10, status: 'COMPLETED' }, signal)).total_amount, '123.4500');
  await api.detail(id, signal);
  await api.processing(id, signal);
  assert.deepEqual(seen, ['/api/backend/sessions/current', '/api/backend/invoices?offset=20&limit=10&status=COMPLETED',
    `/api/backend/invoices/${id}`, `/api/backend/invoices/${id}/processing`]);
  assert.equal(api.documentURL(id), `/api/backend/invoices/${id}/document`);
  assert.throws(() => api.documentURL('../sessions'), /invoice ID/i);
});

test('login, logout and retry use exact methods and CSRF without exposing cookies', async t => {
  const { api } = await load();
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    calls.push([url, init]);
    assert.equal(new Headers(init.headers).get('cookie'), null);
    return init.method === 'DELETE' ? new Response(null, { status: 204 }) : Response.json({});
  });
  await api.login('intake', 'password');
  assert.equal(await api.logout('csrf'), undefined);
  await api.retry(id, 'csrf');
  assert.deepEqual(calls.map(([url, init]) => [url, init.method]), [
    ['/api/backend/sessions', 'POST'], ['/api/backend/sessions/current', 'DELETE'],
    [`/api/backend/invoices/${id}/retry`, 'POST'],
  ]);
  assert.deepEqual(JSON.parse(calls[0][1].body), { username: 'intake', password: 'password' });
  assert.equal(new Headers(calls[1][1].headers).get('X-CSRF-Token'), 'csrf');
  assert.equal(new Headers(calls[2][1].headers).get('X-CSRF-Token'), 'csrf');
});

test('normalizes invoice, session, validation and non-JSON errors without raw server text', async t => {
  const { api, ApiError } = await load();
  const cases = [
    [401, { detail: 'Authentication required' }, 'http_401', 'Authentication required', false],
    [403, { error: { code: 'authorization_failed', message: 'Denied', retryable: false, correlation_id: 'trace' } }, 'authorization_failed', 'Denied', false],
    [422, { detail: [{ msg: 'raw input secret' }] }, 'http_422', 'Invalid request parameters', false],
    [413, '<secret>', 'http_413', 'Invoice is too large', false],
    [415, '<secret>', 'http_415', 'Unsupported document type', false],
    [429, '<secret>', 'http_429', 'Too many requests; try again later', true],
    [503, '<secret>', 'http_503', 'Service temporarily unavailable', true],
  ];
  for (const [status, body, code, message, retryable] of cases) {
    t.mock.method(globalThis, 'fetch', async () => typeof body === 'string'
      ? new Response(body, { status }) : Response.json(body, { status }));
    await assert.rejects(api.currentSession(), error => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.equal(error.code, code);
      assert.equal(error.message, message);
      assert.equal(error.retryable, retryable);
      assert.equal(error.correlationId, status === 403 ? 'trace' : null);
      return true;
    });
  }
});

test('network failures are safe, aborted reads remain AbortError, malformed success is not accepted', async t => {
  const { api } = await load();
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('internal secret'); });
  await assert.rejects(api.currentSession(), { code: 'network_error', status: 0, retryable: true });
  t.mock.method(globalThis, 'fetch', async () => { throw new DOMException('cancelled', 'AbortError'); });
  await assert.rejects(api.currentSession(), { name: 'AbortError' });
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>secret</html>'));
  await assert.rejects(api.currentSession(), { code: 'invalid_response' });
});

test('XHR reports real bytes, caller stable idempotency and CSRF; retries never generate a new key', async t => {
  const { api } = await load();
  const instances = [];
  class XHR {
    upload = {}; headers = {}; status = 202; responseText = '{"invoice_id":"case","processing_status":"QUEUED"}';
    constructor() { instances.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(key, value) { this.headers[key] = value; }
    getResponseHeader() { return null; }
    send(body) {
      this.body = body;
      this.upload.onprogress({ lengthComputable: true, loaded: 42, total: 100 });
      this.onload();
    }
  }
  mockXHR(t, XHR);
  const progress = [];
  const file = new File(['pdf'], 'invoice.pdf', { type: 'application/pdf' });
  for (let i = 0; i < 2; i++) {
    const result = await api.uploadInvoice(file, { csrfToken: 'csrf', idempotencyKey: 'same-key',
      onProgress: value => progress.push(value) });
    assert.equal(result.processing_status, 'QUEUED');
  }
  assert.deepEqual(progress, [{ loaded: 42, total: 100 }, { loaded: 42, total: 100 }]);
  for (const xhr of instances) {
    assert.equal(xhr.url, '/api/backend/invoices');
    assert.equal(xhr.method, 'POST');
    assert.equal(xhr.withCredentials, true);
    assert.equal(xhr.headers['X-CSRF-Token'], 'csrf');
    assert.equal(xhr.headers['Idempotency-Key'], 'same-key');
    assert.equal(xhr.headers['Content-Type'], undefined);
    assert.equal(xhr.body.get('file').name, 'invoice.pdf');
    assert.ok(xhr.timeout > 0);
  }
});

test('XHR rejects missing stable key and surfaces HTTP, network and timeout failures', async t => {
  const { api } = await load();
  let mode = 'http';
  class XHR {
    upload = {}; status = 429; responseText = '<secret>';
    open() {} setRequestHeader() {} getResponseHeader() { return null; }
    send() { if (mode === 'http') this.onload(); else if (mode === 'network') this.onerror(); else this.ontimeout(); }
  }
  mockXHR(t, XHR);
  const file = new File(['pdf'], 'invoice.pdf', { type: 'application/pdf' });
  await assert.rejects(api.uploadInvoice(file, { csrfToken: 'csrf', idempotencyKey: '' }), { code: 'invalid_idempotency_key' });
  for (const [value, code] of [['http', 'http_429'], ['network', 'network_error'], ['timeout', 'timeout']]) {
    mode = value;
    await assert.rejects(api.uploadInvoice(file, { csrfToken: 'csrf', idempotencyKey: 'stable' }), { code, retryable: true });
  }
});
