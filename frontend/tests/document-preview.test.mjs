import test from 'node:test';
import assert from 'node:assert/strict';
import { documentMarkup, DOCUMENT_ID } from './document-preview-fixture.mjs';
import { loadTypeScript } from './load-typescript.mjs';

const url = `http://localhost:3000/api/backend/invoices/${DOCUMENT_ID}/document`;
const request = () => new Request(url, { headers: { cookie: 'demo_session=opaque' } });

test('native document preview does not apply the plugin-blocking iframe sandbox; safe links remain', async () => {
  const markup = await documentMarkup();
  assert.match(markup, /<iframe[^>]+src="\/api\/backend\/invoices\/[^"]+\/document"/);
  assert.doesNotMatch(markup, /\bsandbox=/);
  assert.match(markup, /referrerPolicy="same-origin"/i);
  assert.match(markup, /target="_blank" rel="noopener noreferrer"/);
  assert.match(markup, /download=""/);
  assert.doesNotMatch(await documentMarkup(false), /<iframe/);
});

test('private original accepts PDF, PNG and JPEG bytes without weakening privacy or upstream frame policy', async t => {
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  for (const type of ['application/pdf', 'image/png', 'image/jpeg']) {
    t.mock.method(globalThis, 'fetch', async (_url, init) => {
      assert.equal(init.headers.get('cookie'), 'demo_session=opaque');
      assert.equal(init.method, 'GET');
      assert.equal(init.cache, 'no-store');
      return new Response(new Uint8Array([0, 255, 17]), { headers: { 'content-type': type,
        'content-disposition': 'inline', 'content-security-policy': "frame-ancestors 'self'",
        'x-frame-options': 'SAMEORIGIN' } });
    });
    const response = await proxyBackend(request());
    assert.equal(response.status, 200);
    assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [0, 255, 17]);
    assert.equal(response.headers.get('content-type'), type);
    assert.equal(response.headers.get('content-disposition'), 'inline');
    assert.equal(response.headers.get('content-security-policy'), "frame-ancestors 'self'");
    assert.equal(response.headers.get('x-frame-options'), 'SAMEORIGIN');
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
    assert.match(response.headers.get('vary'), /Cookie/);
  }
});

test('successful original with unexpected or absent MIME fails closed without leaking its bytes', async t => {
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  let calls = 0;
  for (const type of ['text/html', 'image/svg+xml', 'application/json', 'application/octet-stream', null]) {
    t.mock.method(globalThis, 'fetch', async () => {
      calls++;
      return new Response(new TextEncoder().encode('<script>private_payload</script>'), {
        headers: { ...(type ? { 'content-type': type } : {}), 'content-disposition': 'inline',
          'x-correlation-id': 'trace' } });
    });
    const response = await proxyBackend(request());
    assert.equal(response.status, 502, type);
    assert.deepEqual(await response.json(), { error: { code: 'unsupported_document_type',
      message: 'Original document could not be displayed safely', retryable: true, correlation_id: 'trace' } });
    assert.equal(response.headers.get('content-type'), 'application/json');
    assert.equal(response.headers.get('content-disposition'), null);
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  }
  assert.equal(calls, 5);
});

test('document authentication and authorization failures remain private JSON, never converted to a preview', async t => {
  const { proxyBackend } = await loadTypeScript('../lib/backend-proxy.ts');
  for (const status of [401, 403, 404]) {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Access denied' }, { status }));
    const response = await proxyBackend(request());
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), { detail: 'Access denied' });
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
  }
});
