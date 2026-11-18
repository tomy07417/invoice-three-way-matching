// Run with the prestarted frontend pointing at this script's isolated fixture.
// Error-matrix/technical-failure responses are CDP simulations, not backend proof.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { launchChrome, stopChild } from './cdp.mjs';

const base = process.env.INTAKE_BASE_URL ?? 'http://127.0.0.1:3212';
assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
const root = fileURLToPath(new URL('../../', import.meta.url));
const testDatabase = process.env.TEST_DATABASE_URL;
assert.ok(testDatabase, 'Set TEST_DATABASE_URL to the isolated test_ PostgreSQL service');
// Explicit environment allowlist: no inherited application DB or Azure configuration.
const backend = spawn(process.env.INTAKE_PYTHON ?? `${root}backend/.venv/bin/python`, [
  fileURLToPath(new URL('./intake-backend.py', import.meta.url)),
], { cwd: root, env: { PATH: '/usr/bin:/bin', PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1', TEST_DATABASE_URL: testDatabase }, stdio: ['ignore', 'pipe', 'pipe'] });
let chrome;
let stage = 'fixture readiness';
let closing;
async function cleanup() {
  if (!closing) closing = (async () => {
    try { await chrome?.close(); }
    finally { await stopChild(backend); }
  })();
  return closing;
}
const overall = setTimeout(() => { console.error(`FAIL timeout at ${stage}`); void cleanup().finally(() => process.exit(1)); }, 180000);
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void cleanup().finally(() => process.exit(1)); });
function pass(message) { console.log(`PASS ${message}`); }

try {
  const fixture = await new Promise((resolve, reject) => {
    let png;
    let bound = false;
    let output = '';
    const timer = setTimeout(() => reject(new Error('Fixture readiness timeout')), 25000);
    const inspect = chunk => {
      output += chunk.toString();
      const match = output.match(/INTAKE_FIXTURE (\{[^\n]+\})/);
      if (match) png = JSON.parse(match[1]).png;
      if (output.includes('Uvicorn running on http://127.0.0.1:3211')) bound = true;
      if (png && bound) { clearTimeout(timer); resolve({ png }); }
    };
    backend.stdout.on('data', inspect);
    backend.stderr.on('data', inspect);
    backend.once('error', () => { clearTimeout(timer); reject(new Error('Fixture launch failed')); });
    backend.once('exit', () => { clearTimeout(timer); reject(new Error('Fixture exited before readiness')); });
  });
  chrome = await launchChrome();
  const cdp = chrome.client;
  const requests = [];
  const scriptErrors = [];
  cdp.on('Network.requestWillBeSent', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.startsWith('/api/backend/')) requests.push({ path: url.pathname, query: url.search, method: request.method, headers: request.headers });
  });
  cdp.on('Runtime.exceptionThrown', () => scriptErrors.push('Unhandled browser exception'));
  let simulatedError = null;
  let remainingFailures = 0;
  let loseFirstAcceptance = true;
  let technicalFailure = false;
  const interceptionErrors = [];
  await cdp.send('Fetch.enable', { patterns: [
    { urlPattern: `${base}/api/backend/*`, requestStage: 'Request' },
    { urlPattern: `${base}/api/backend/*`, requestStage: 'Response' },
  ] });
  cdp.on('Fetch.requestPaused', event => {
    void (async () => {
      const path = new URL(event.request.url).pathname;
      const responseStage = event.responseStatusCode !== undefined;
      const fail = async status => cdp.send('Fetch.fulfillRequest', {
        requestId: event.requestId, responseCode: status,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(JSON.stringify({ error: { code: `browser_${status}`, message: 'Controlled browser failure', retryable: status >= 500 || status === 429 } })).toString('base64'),
      });
      if (!responseStage && simulatedError !== null && path === '/api/backend/invoices' && event.request.method === 'GET') {
        const status = simulatedError;
        remainingFailures -= 1;
        if (remainingFailures <= 0) simulatedError = null;
        if (status === 0) await cdp.send('Fetch.failRequest', { requestId: event.requestId, errorReason: 'ConnectionFailed' });
        else await fail(status);
      } else if (responseStage && loseFirstAcceptance && path === '/api/backend/invoices' && event.request.method === 'POST' && event.responseStatusCode === 202) {
        loseFirstAcceptance = false;
        await fail(503); // Backend accepted it; browser did not receive the success.
      } else if (responseStage && technicalFailure && /^\/api\/backend\/invoices\/[0-9a-f-]{36}$/.test(path) && event.responseStatusCode === 200) {
        const body = await cdp.send('Fetch.getResponseBody', { requestId: event.requestId });
        const detail = JSON.parse(body.base64Encoded ? Buffer.from(body.body, 'base64').toString() : body.body);
        Object.assign(detail, { processing_status: 'PROCESSING_FAILED', automatic_outcome: null,
          error: { code: 'processing_failed', message: 'Controlled technical failure', retryable: false } });
        await cdp.send('Fetch.fulfillRequest', { requestId: event.requestId, responseCode: 200,
          responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(detail)).toString('base64') });
      } else await cdp.send('Fetch.continueRequest', { requestId: event.requestId });
    })().catch(() => { interceptionErrors.push('Interception failed'); });
  });
  const click = label => cdp.evaluate(`Array.from(document.querySelectorAll('button,a')).find(node => node.textContent.trim() === ${JSON.stringify(label)})?.click()`);
  const text = value => `document.body.innerText.includes(${JSON.stringify(value)})`;
  const uploadPosts = () => requests.filter(request => request.path === '/api/backend/invoices' && request.method === 'POST');
  function listResponse(fragment) {
    return new Promise((resolve, reject) => {
      const off = cdp.on('Network.responseReceived', ({ response }) => {
        const url = new URL(response.url);
        if (url.pathname === '/api/backend/invoices' && url.search.includes(fragment)) {
          clearTimeout(timer); off(); resolve();
        }
      });
      const timer = setTimeout(() => { off(); reject(new Error('List response timeout')); }, 15000);
    });
  }
  async function login(username) {
    await cdp.wait(`!!document.querySelector('#username')`);
    await cdp.evaluate(`(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      for (const [id, value] of [['username', ${JSON.stringify(username)}], ['password', 'browser test password']]) {
        const input = document.getElementById(id); setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      document.querySelector('form').requestSubmit();
    })()`);
    await cdp.wait(`!!document.querySelector('#processing-filter')`);
    await cdp.wait(`!document.body.innerText.includes('Loading invoice data…')`);
  }

  stage = 'authentication and empty list';
  await cdp.navigate(`${base}/invoices`);
  await login('browser_owner');
  await cdp.wait(text('No invoices on this page'));
  const cookies = await cdp.send('Network.getCookies', { urls: [base] });
  assert.equal(cookies.cookies.find(cookie => cookie.name === 'demo_session')?.httpOnly, true);
  assert.equal(await cdp.evaluate(`document.cookie.includes('demo_session')`), false);
  pass('real proxy authentication, HttpOnly cookie and empty owner list');

  stage = 'local validation and uncertain upload';
  await click('New invoice');
  await cdp.wait(`!!document.querySelector('#invoice-file')`);
  assert.equal(await cdp.evaluate(`document.querySelector('label[for="invoice-file"]').textContent`), 'Invoice document');
  await cdp.evaluate(`(() => { const transfer = new DataTransfer(); transfer.items.add(new File(['bad'], 'invalid.txt', { type: 'text/plain' })); document.querySelector('.drop-zone').dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: transfer })); })()`);
  await cdp.wait(text('Choose a PDF, PNG or JPG'));
  await cdp.evaluate(`(() => { const transfer = new DataTransfer(); transfer.items.add(new File([new Uint8Array(8388609)], 'large.png', { type: 'image/png' })); document.querySelector('.drop-zone').dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: transfer })); })()`);
  await cdp.wait(text('at most 8 MiB'));
  assert.equal(uploadPosts().length, 0);
  await cdp.setFile('#invoice-file', fixture.png);
  await cdp.evaluate(`(() => {
    window.__sawProgress = false;
    const observer = new MutationObserver(() => { if (document.querySelector('#upload-progress')) { window.__sawProgress = true; observer.disconnect(); } });
    observer.observe(document, { subtree: true, childList: true });
    const button = document.querySelector('button[type="submit"]'); button.click(); button.click();
  })()`);
  await cdp.wait(text('temporarily unavailable'));
  assert.equal(uploadPosts().length, 1);
  assert.equal(await cdp.evaluate('window.__sawProgress'), true);
  assert.equal(await cdp.evaluate(`fetch('/api/backend/invoices').then(r => r.json()).then(page => page.total)`), 1);
  await click('Upload invoice');
  await cdp.wait(text('Invoice received'));
  const id = await cdp.evaluate(`document.querySelector('[aria-label="Upload success"] a').textContent`);
  assert.match(id, /^[0-9a-f-]{36}$/);
  assert.equal(uploadPosts().length, 2);
  const header = (request, name) => Object.entries(request.headers).find(([key]) => key.toLowerCase() === name)?.[1];
  assert.equal(header(uploadPosts()[0], 'idempotency-key'), header(uploadPosts()[1], 'idempotency-key'));
  assert.ok(header(uploadPosts()[0], 'x-csrf-token'));
  assert.equal(await cdp.evaluate(`fetch('/api/backend/invoices').then(r => r.json()).then(page => page.total)`), 1);
  pass('local invalid/oversize rejection, same-frame double click, observed progress, stable key after accepted-but-lost 503, one persisted case');

  stage = 'automatic processing and evidence';
  await click(id);
  await cdp.wait(text('Technical state'));
  assert.equal(await cdp.evaluate(`${text('EXTRACTING')} || ${text('QUEUED')}`), true);
  await cdp.wait(text('Held for Review'), 25000);
  await cdp.wait(text('60.000000'));
  for (const value of ['Browser item', 'PO-BROWSER', 'Controlled extraction warning.', 'Reasons and evidence']) assert.equal(await cdp.evaluate(text(value)), true);
  assert.equal(await cdp.evaluate(`!!document.querySelector('.findings li') && document.querySelector('.findings li').innerText.length > 35`), true);
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Confirm scheduled retry'))`), false);
  assert.equal(await cdp.evaluate(`fetch(${JSON.stringify(`/api/backend/invoices/${id}/document`)}).then(async r => r.ok && r.headers.get('content-type').includes('image/png') && (await r.arrayBuffer()).byteLength > 0)`), true);
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('a')).some(a => a.hasAttribute('download') && a.getAttribute('href') === ${JSON.stringify(`/api/backend/invoices/${id}/document`)})`), true);
  const countReads = () => requests.filter(request => request.path.startsWith(`/api/backend/invoices/${id}`) && !request.path.endsWith('/document')).length;
  const terminalReads = countReads();
  await cdp.evaluate('new Promise(resolve => setTimeout(resolve, 6500))');
  assert.equal(countReads(), terminalReads);
  pass('real durable worker/evaluator, automatic active-to-terminal transition, held reasons/lines/PO/exact decimals, protected document and terminal polling stop');

  stage = 'pagination and filter';
  assert.equal(await cdp.evaluate(`(async () => {
    const session = await fetch('/api/backend/sessions/current').then(r => r.json());
    const original = await fetch(${JSON.stringify(`/api/backend/invoices/${id}/document`)}).then(r => r.blob());
    for (let index = 0; index < 20; index++) {
      const form = new FormData(); form.append('file', original, 'seed.png');
      const response = await fetch('/api/backend/invoices', { method: 'POST', headers: { 'X-CSRF-Token': session.csrf_token, 'Idempotency-Key': crypto.randomUUID() }, body: form });
      if (response.status !== 202) return false;
    }
    return true;
  })()`), true);
  await click('Back to invoices');
  await cdp.wait(text('21 invoices'));
  const nextResponse = listResponse('offset=20');
  await click('Next');
  await nextResponse;
  await cdp.wait(`${text('Page 2')} && document.querySelectorAll('tbody tr').length === 1`);
  assert.ok(requests.some(request => request.query.includes('offset=20') && request.query.includes('limit=20')));
  const filteredResponse = listResponse('status=COMPLETED');
  await cdp.evaluate(`(() => { const select = document.querySelector('#processing-filter'); select.value = 'COMPLETED'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await filteredResponse;
  await cdp.wait(`!!document.querySelector('tbody') || ${text('No invoices on this page')}`);
  assert.ok(requests.some(request => request.query.includes('status=COMPLETED') && request.query.includes('offset=0')));
  pass('real public upload seeding, list recovery, 20-item pagination and technical-state query');

  stage = 'simulated recoverable errors';
  for (const status of [0, 403, 413, 415, 422, 429, 503]) {
    simulatedError = status;
    remainingFailures = 1;
    await click('Refresh');
    await cdp.wait(`!!document.querySelector('[role="alert"]')`);
    await click('Refresh');
    await cdp.wait(`!document.querySelector('[role="alert"]') && !document.body.innerText.includes('Loading invoice data…')`);
  }
  simulatedError = 503;
  remainingFailures = 3;
  await click('Refresh');
  await cdp.wait(text('paused after three read errors'), 25000);
  await click('Refresh');
  await cdp.wait(`!document.querySelector('[role="alert"]') && !document.body.innerText.includes('Loading invoice data…')`);
  technicalFailure = true;
  await cdp.navigate(`${base}/invoices/${id}`);
  await cdp.wait(text('PROCESSING_FAILED'));
  assert.equal(await cdp.evaluate(text('Rejected')), false);
  technicalFailure = false;
  pass('CDP-only network/403/413/415/422/429/503 recovery, three-error pause/manual resume and technical failure without Rejected');

  stage = 'mobile keyboard and ownership';
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await cdp.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
  assert.equal(await cdp.evaluate(`document.activeElement !== document.body && getComputedStyle(document.activeElement).outlineStyle !== 'none'`), true);
  assert.equal(await cdp.evaluate(`/approve|correct extraction|edit supplier|master data/i.test(Array.from(document.querySelectorAll('button,a')).map(n => n.textContent).join(' '))`), false);
  await click('Log out');
  await login('browser_other');
  await cdp.wait(text('No invoices on this page'));
  await cdp.navigate(`${base}/invoices/${id}`);
  await cdp.wait(text('Invoice not found'));
  await cdp.send('Network.deleteCookies', { name: 'demo_session', url: base });
  await click('Refresh');
  await cdp.wait(`!!document.querySelector('#username')`);
  assert.equal(await cdp.evaluate(`new URLSearchParams(location.search).get('returnTo')`), `/invoices/${id}`);
  assert.equal(scriptErrors.length, 0);
  assert.equal(interceptionErrors.length, 0);
  pass('mobile root bounds, keyboard focus, no AP controls, CSRF logout, other-owner empty/404 and real later-401 safe return path');
  console.log('Browser acceptance finished; controlled extractor only, no Azure proof claimed.');
} catch {
  console.error(`FAIL ${stage} (private browser/connection values withheld)`);
  process.exitCode = 1;
} finally {
  clearTimeout(overall);
  await cleanup();
}
