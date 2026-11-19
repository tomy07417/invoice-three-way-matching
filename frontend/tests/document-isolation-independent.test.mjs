import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const id = '11111111-2222-4333-8444-555555555555';
const path = `/invoices/${id}/document`;
const request = (suffix = path, headers = {}) => new Request('https://app.example/api/backend' + suffix, { headers });
const privacy = response => {
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.match(response.headers.get('vary'), /Cookie/);
  assert.equal(response.headers.get('content-security-policy'), "frame-ancestors 'none'");
  assert.equal(response.headers.get('x-frame-options'), 'DENY');
};
const policy = { 'content-security-policy': "frame-ancestors 'none'", 'x-frame-options': 'DENY' };

test('S1-S4 independent: normalized MIME, exact URL and isolated session-cookie forwarding', async t => {
  const { api } = await loadTypeScript('../lib/api.ts');
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  assert.equal(api.documentURL(id), '/api/backend' + path);
  for (const bad of ['', 'not-a-uuid', id + '/document', 'https://evil.example', id + '?x=1']) {
    assert.throws(() => api.documentURL(bad), { name: 'TypeError', message: 'Invalid invoice ID' });
  }
  for (const type of ['Application/PDF; version=1.7', 'IMAGE/PNG; extra=value', 'image/jpeg; charset=binary']) {
    t.mock.method(globalThis, 'fetch', async (url, init) => {
      assert.equal(new URL(url).pathname, path);
      assert.equal(init.headers.get('cookie'), 'demo_session=synthetic');
      assert.equal(init.headers.get('authorization'), null);
      assert.equal(init.redirect, 'manual');
      return new Response(new Uint8Array([37, 80, 68, 70, 0, 255]), { headers: { ...policy, 'content-type': type } });
    });
    const response = await proxyBackend(request(path, { cookie: 'tracking=discard; demo_session=synthetic', authorization: 'discard' }));
    assert.equal(response.status, 200);
    assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [37, 80, 68, 70, 0, 255]);
    privacy(response);
  }
});

test('S3-S4 independent: successful MIME near-misses fail safely and retain upstream frame restrictions', async t => {
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  for (const type of [null, '', 'text/html; charset=utf-8', 'IMAGE/SVG+XML', 'application/pdfx', 'application/pdf, text/html']) {
    t.mock.method(globalThis, 'fetch', async () => new Response(new TextEncoder().encode('PRIVATE_RAW_MARKER'), {
      status: 206, headers: { ...policy, ...(type === null ? {} : { 'content-type': type }), 'content-disposition': 'inline; filename="private.html"' },
    }));
    const response = await proxyBackend(request());
    assert.equal(response.status, 502, String(type));
    assert.equal(await response.text(), JSON.stringify({ error: { code: 'unsupported_document_type',
      message: 'Original document could not be displayed safely', retryable: true, correlation_id: null } }));
    assert.equal(response.headers.get('content-type'), 'application/json');
    assert.equal(response.headers.get('content-disposition'), null);
    privacy(response);
  }
});

test('S2-S4 independent: missing session, HTML auth errors, sibling successes and rejected document routes', async t => {
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  for (const status of [401, 403, 404]) {
    t.mock.method(globalThis, 'fetch', async (_url, init) => {
      assert.equal(init.headers.get('cookie'), null);
      return new Response('PRIVATE_AUTH_MARKER', { status, headers: { ...policy, 'content-type': 'text/html', 'content-disposition': 'inline' } });
    });
    const response = await proxyBackend(request());
    assert.equal(response.status, status);
    assert.equal(await response.text(), JSON.stringify({ error: { code: `http_${status}`,
      message: 'Request could not be completed', retryable: false, correlation_id: null } }));
    assert.equal(response.headers.get('content-disposition'), null);
    privacy(response);
  }
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response('sibling', { headers: { 'content-type': 'text/html' } }); });
  const sibling = await proxyBackend(request('/invoices'));
  assert.equal(sibling.status, 200);
  assert.equal(await sibling.text(), 'sibling');
  for (const [suffix, status] of [[path + '?url=https://evil.example', 400], [path + '/', 404], ['/invoices/not-a-uuid/document', 404]]) {
    const response = await proxyBackend(request(suffix));
    assert.equal(response.status, status);
  }
  assert.equal(calls, 1);
});
