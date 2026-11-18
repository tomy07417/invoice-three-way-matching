import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';

const uuid = '11111111-2222-4333-8444-555555555555';
const base = 'http://localhost:3000/api/backend';
const request = (path, method = 'GET', headers = {}, body) => new Request(base + path, {
  method, headers: { ...(method === 'GET' ? {} : { origin: 'http://localhost:3000' }), ...headers },
  body, ...(body instanceof ReadableStream ? { duplex: 'half' } : {}),
});
const load = () => loadTypeScript('../lib/backend-proxy.ts');
function privateResponse(response) {
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.match(response.headers.get('vary'), /Cookie/i);
}

test('allowlisted methods and UUID paths only; no upstream on rejected input', async t => {
  const { proxyBackend } = await load();
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('{}'); });
  for (const path of ['/sessions', '/sessions/current', '/invoices', `/invoices/${uuid}`,
    `/invoices/${uuid}/processing`, `/invoices/${uuid}/document`]) {
    const method = path === '/sessions' ? 'POST' : 'GET';
    assert.equal((await proxyBackend(request(path, method))).status, 200);
  }
  assert.equal((await proxyBackend(request('/sessions/current', 'DELETE'))).status, 200);
  assert.equal((await proxyBackend(request(`/invoices/${uuid}/retry`, 'POST'))).status, 200);
  const before = calls;
  for (const path of ['/health', '/invoices/not-a-uuid', '/invoices/%2fsecret',
    '/invoices/%252e%252e', '//evil.test/invoices', `/invoices/${uuid}/retry/extra`]) {
    assert.equal((await proxyBackend(request(path))).status, 404);
  }
  assert.equal((await proxyBackend(request('/invoices', 'DELETE'))).status, 405);
  assert.equal(calls, before);
});

test('configured origin is server-only and cannot contain credentials, path, query or fragment', async t => {
  const { proxyBackend } = await load();
  const original = process.env.BACKEND_INTERNAL_URL;
  t.after(() => original === undefined ? delete process.env.BACKEND_INTERNAL_URL : process.env.BACKEND_INTERNAL_URL = original);
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('{}'); });
  for (const origin of ['https://user:pass@backend:8000', 'http://backend:8000/private',
    'http://backend:8000?x=1', 'http://backend:8000#secret', 'file:///private']) {
    process.env.BACKEND_INTERNAL_URL = origin;
    const response = await proxyBackend(request('/invoices'));
    assert.equal(response.status, 503);
    assert.doesNotMatch(await response.text(), /pass|private|secret/);
    privateResponse(response);
  }
  assert.equal(calls, 0);
});

test('mutations including login require explicit same Origin; forwarded host is not authority', async t => {
  const { proxyBackend } = await load();
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('{}'); });
  for (const origin of ['https://evil.test', 'null', '']) {
    const response = await proxyBackend(request('/sessions', 'POST', { origin, 'x-forwarded-host': 'evil.test' }));
    assert.equal(response.status, 403);
    privateResponse(response);
  }
  assert.equal(calls, 0);
});

test('trusted frontend origin admits browser login despite Next internal hostname', async t => {
  const { proxyBackend } = await load();
  const original = process.env.FRONTEND_ORIGIN;
  t.after(() => original === undefined ? delete process.env.FRONTEND_ORIGIN : process.env.FRONTEND_ORIGIN = original);
  process.env.FRONTEND_ORIGIN = 'http://127.0.0.1:3212';
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    calls++;
    assert.equal(new URL(url).pathname, '/sessions');
    assert.equal(init.method, 'POST');
    assert.equal(init.headers.get('x-forwarded-host'), null);
    assert.equal(new TextDecoder().decode(init.body), '{"username":"intake","password":"test-password"}');
    return Response.json({ actor: { username: 'intake' } }, { headers: {
      'set-cookie': 'demo_session=opaque; HttpOnly; SameSite=Lax; Path=/',
    } });
  });
  const login = origin => new Request('http://localhost:3212/api/backend/sessions', {
    method: 'POST', headers: { origin, 'x-forwarded-host': 'evil.test', 'content-type': 'application/json' },
    body: '{"username":"intake","password":"test-password"}',
  });
  const response = await proxyBackend(login('http://127.0.0.1:3212'));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { actor: { username: 'intake' } });
  assert.match(response.headers.getSetCookie()[0], /HttpOnly; SameSite=Lax/);
  privateResponse(response);
  for (const origin of ['https://evil.test', 'http://localhost:3212', 'null', '']) {
    const denied = await proxyBackend(login(origin));
    assert.equal(denied.status, 403);
    assert.deepEqual(await denied.json(), { error: { code: 'invalid_origin',
      message: 'Same-origin request required', retryable: false, correlation_id: null } });
    privateResponse(denied);
  }
  assert.equal(calls, 1);
});

test('invalid trusted frontend origin fails closed without forwarding or leaking configuration or login body', async t => {
  const { proxyBackend } = await load();
  const original = process.env.FRONTEND_ORIGIN;
  t.after(() => original === undefined ? delete process.env.FRONTEND_ORIGIN : process.env.FRONTEND_ORIGIN = original);
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('{}'); });
  for (const value of ['', 'not-an-origin', 'https://user:secret@frontend.test',
    'http://frontend.test/private', 'http://frontend.test/../', 'http://frontend.test?secret',
    'http://frontend.test?', 'http://frontend.test#secret', 'http://frontend.test#',
    'file:///private', ' http://frontend.test', 'http://frontend.test\\\\private']) {
    process.env.FRONTEND_ORIGIN = value;
    const response = await proxyBackend(request('/sessions', 'POST', {
      'content-type': 'application/json',
    }, '{"password":"test-password"}'));
    assert.equal(response.status, 503, value);
    assert.deepEqual(await response.json(), { error: { code: 'proxy_configuration',
      message: 'Service temporarily unavailable', retryable: true, correlation_id: null } });
    privateResponse(response);
  }
  assert.equal(calls, 0);
});

test('only session cookie and explicit request headers cross boundary; safe query and no-store', async t => {
  const { proxyBackend } = await load();
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(new URL(url).pathname, '/invoices');
    assert.equal(new URL(url).search, '?offset=20&limit=10&status=QUEUED');
    assert.equal(init.headers.get('cookie'), 'demo_session=opaque');
    assert.equal(init.headers.get('authorization'), null);
    assert.equal(init.headers.get('x-forwarded-host'), null);
    assert.equal(init.cache, 'no-store');
    assert.equal(init.redirect, 'manual');
    assert.ok(init.signal instanceof AbortSignal);
    return new Response('{}');
  });
  privateResponse(await proxyBackend(request('/invoices?offset=20&limit=10&status=QUEUED', 'GET', {
    cookie: 'other=secret; demo_session=opaque; tracking=foo', authorization: 'Bearer secret',
  })));
  assert.equal((await proxyBackend(request('/invoices?url=https://evil.test'))).status, 400);
});

test('POST preserves CSRF, stable idempotency key and body; DELETE remains bodyless', async t => {
  const { proxyBackend } = await load();
  t.mock.method(globalThis, 'fetch', async (_url, init) => {
    assert.equal(init.headers.get('x-csrf-token'), 'csrf');
    if (init.method === 'POST') {
      assert.equal(init.headers.get('idempotency-key'), 'stable');
      assert.equal(init.headers.get('content-type'), 'application/octet-stream');
      assert.equal(new TextDecoder().decode(init.body), 'bytes');
    } else assert.equal(init.body, undefined);
    return new Response(null, { status: 204 });
  });
  assert.equal((await proxyBackend(request('/invoices', 'POST', {
    'x-csrf-token': 'csrf', 'idempotency-key': 'stable', 'content-type': 'application/octet-stream',
  }, 'bytes'))).status, 204);
  assert.equal((await proxyBackend(request('/sessions/current', 'DELETE', { 'x-csrf-token': 'csrf' }))).status, 204);
});

test('binary downloads and multiple cookies preserve safe content/security headers and status', async t => {
  const { proxyBackend } = await load();
  t.mock.method(globalThis, 'fetch', async () => {
    const headers = new Headers({ 'content-type': 'application/pdf', 'content-disposition': 'inline',
      'x-content-type-options': 'nosniff', vary: 'Accept', 'x-correlation-id': 'trace',
      'x-internal-debug': 'private' });
    headers.append('set-cookie', 'demo_session=a; HttpOnly; SameSite=Lax; Path=/');
    headers.append('set-cookie', 'demo_session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/');
    return new Response(new Uint8Array([0, 255, 17]), { status: 200, headers });
  });
  const response = await proxyBackend(request(`/invoices/${uuid}/document`));
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [0, 255, 17]);
  assert.equal(response.headers.getSetCookie().length, 2);
  assert.match(response.headers.getSetCookie()[0], /HttpOnly; SameSite=Lax/);
  assert.equal(response.headers.get('content-type'), 'application/pdf');
  assert.equal(response.headers.get('content-disposition'), 'inline');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(response.headers.get('x-internal-debug'), null);
  privateResponse(response);
});

test('redirects never follow or leak Location; non-JSON upstream errors are sanitized', async t => {
  const { proxyBackend } = await load();
  t.mock.method(globalThis, 'fetch', async () => new Response('provider secret', {
    status: 302, headers: { location: 'https://private.test' },
  }));
  const redirect = await proxyBackend(request('/invoices'));
  assert.equal(redirect.status, 502);
  assert.equal(redirect.headers.get('location'), null);
  assert.doesNotMatch(await redirect.text(), /provider secret|private.test/);
  t.mock.method(globalThis, 'fetch', async () => new Response('<secret>', { status: 503 }));
  const response = await proxyBackend(request('/invoices'));
  assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /secret/);
  privateResponse(response);
});

test('body limits count actual streamed bytes and reject malformed or oversized Content-Length', async t => {
  const { proxyBackend, MAX_PROXY_BODY_BYTES } = await load();
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('{}'); });
  for (const length of ['bad', '-1', '1.5', String(MAX_PROXY_BODY_BYTES + 1)]) {
    const response = await proxyBackend(request('/invoices', 'POST', { 'content-length': length }, 'x'));
    assert.equal(response.status, length === String(MAX_PROXY_BODY_BYTES + 1) ? 413 : 400);
    privateResponse(response);
  }
  const body = new ReadableStream({ start(controller) {
    controller.enqueue(new Uint8Array(MAX_PROXY_BODY_BYTES + 1)); controller.close();
  } });
  assert.equal((await proxyBackend(request('/invoices', 'POST', {}, body))).status, 413);
  assert.equal(calls, 0);
});

test('whole-request deadline cancels a stalled incoming upload before contacting backend', async t => {
  const { proxyBackend } = await load();
  const controller = new AbortController();
  let calls = 0;
  let cancelled = false;
  t.mock.method(AbortSignal, 'timeout', milliseconds => {
    assert.equal(milliseconds, 15_000);
    queueMicrotask(() => controller.abort(new DOMException('deadline', 'TimeoutError')));
    return controller.signal;
  });
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('{}'); });
  const stream = new ReadableStream({ cancel() { cancelled = true; } });
  const response = await proxyBackend(request('/invoices', 'POST', {}, stream));
  assert.equal(response.status, 504);
  assert.equal(cancelled, true);
  assert.equal(calls, 0);
  privateResponse(response);
});

test('normalized API and session errors retain status, correlation and retry guidance', async t => {
  const { proxyBackend } = await load();
  for (const [status, data] of [[429, { error: { code: 'queue_full', message: 'Processing queue is full',
    retryable: true, correlation_id: 'trace' } }], [401, { detail: 'Authentication required' }],
    [422, { detail: [{ msg: 'Invalid parameters' }] }]]) {
    t.mock.method(globalThis, 'fetch', async () => Response.json(data, {
      status, headers: { 'x-correlation-id': 'trace', 'retry-after': '20' },
    }));
    const response = await proxyBackend(request('/invoices'));
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), data);
    assert.equal(response.headers.get('x-correlation-id'), 'trace');
    assert.equal(response.headers.get('retry-after'), '20');
    privateResponse(response);
  }
  t.mock.method(globalThis, 'fetch', async () => new Response('<secret>', {
    status: 429, headers: { 'retry-after': '20', 'x-correlation-id': 'trace' },
  }));
  const response = await proxyBackend(request('/invoices'));
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '20');
  assert.equal((await response.json()).error.correlation_id, 'trace');
});

test('network and bounded timeout failures are safe private responses', async t => {
  const { proxyBackend } = await load();
  for (const [error, status] of [[new Error('secret connection'), 502],
    [new DOMException('secret timeout', 'TimeoutError'), 504]]) {
    t.mock.method(globalThis, 'fetch', async () => { throw error; });
    const response = await proxyBackend(request('/invoices'));
    assert.equal(response.status, status);
    assert.doesNotMatch(await response.text(), /secret/);
    privateResponse(response);
  }
});
