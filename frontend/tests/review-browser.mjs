// Native Chrome acceptance: real Next proxy, FastAPI, owned PostgreSQL and controlled extraction.
// Requires a prestarted test frontend with BACKEND_INTERNAL_URL=http://127.0.0.1:3211.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { launchChrome, stopChild } from './cdp.mjs';

const base = process.env.INTAKE_BASE_URL ?? 'http://127.0.0.1:3212';
assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
assert.ok(process.env.TEST_DATABASE_URL, 'An isolated test_ PostgreSQL URL is required');
const focus = process.env.AP_REVIEW_FOCUS;
assert.ok(!focus || focus === 'comparison', 'Only the comparison focus is supported');
const root = fileURLToPath(new URL('../../', import.meta.url));
const backend = spawn(`${root}backend/.venv/bin/python`, [fileURLToPath(new URL('./intake-backend.py', import.meta.url))], {
  cwd: root, env: { PATH: '/usr/bin:/bin', PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1',
    HOME: process.env.HOME, TMPDIR: process.env.TMPDIR,
    TEST_DATABASE_URL: process.env.TEST_DATABASE_URL, AP_REVIEW_FIXTURE: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
});
let chrome;
let stage = 'fixture readiness';
let closing;
const cleanup = () => closing ??= (async () => { try { await chrome?.close(); } finally { await stopChild(backend); } })();
const deadline = setTimeout(() => { console.error(`FAIL timeout at ${stage}`); void cleanup().finally(() => process.exit(1)); }, 180000);
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void cleanup().finally(() => process.exit(1)); });
const pass = value => console.log('PASS ' + value);
async function verify() {
  const png = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Fixture readiness timeout')), 25000);
    const inspect = chunk => {
      output = (output + chunk.toString()).slice(-16000);
      const match = output.match(/INTAKE_FIXTURE (\{[^\n]+\})/);
      if (match && output.includes('Uvicorn running on http://127.0.0.1:3211')) { clearTimeout(timer); resolve(JSON.parse(match[1]).png); }
    };
    backend.stdout.on('data', inspect); backend.stderr.on('data', inspect);
    backend.once('error', () => { clearTimeout(timer); reject(new Error('Fixture launch failed')); });
    backend.once('exit', () => { clearTimeout(timer); reject(new Error('Fixture exited')); });
  });
  chrome = await launchChrome();
  const cdp = chrome.client;
  const errors = [];
  const requests = [];
  cdp.on('Runtime.exceptionThrown', () => errors.push('Unhandled browser exception'));
  cdp.on('Network.requestWillBeSent', ({ request }) => {
    const path = new URL(request.url).pathname;
    if (path.startsWith('/api/backend/')) requests.push({ path, method: request.method });
  });
  cdp.on('Page.javascriptDialogOpening', ({ message }) => {
    const known = /^(Confirm payment authorization|Reject invoice|Request credit note|Save correction, revoke|Reevaluation preserves|Refresh discards)/.test(message);
    if (!known) errors.push('Unexpected confirmation');
    void cdp.send('Page.handleJavaScriptDialog', { accept: known });
  });
  const text = value => `document.body.innerText.includes(${JSON.stringify(value)})`;
  const click = label => cdp.evaluate(`Array.from(document.querySelectorAll('button,a')).find(node => node.textContent.trim() === ${JSON.stringify(label)})?.click()`);
  async function fill(selector, value) {
    await cdp.evaluate(`(() => { const node = document.querySelector(${JSON.stringify(selector)}); node.focus(); node.select(); })()`);
    await cdp.send('Input.insertText', { text: value });
  }
  async function login(username, { navigate = true, path = '/invoices' } = {}) {
    if (navigate) await cdp.navigate(base + '/login');
    await cdp.wait(`!!document.querySelector('#username')`);
    await fill('#username', username); await fill('#password', 'browser test password');
    await cdp.evaluate(`document.querySelector('form').requestSubmit()`);
    await cdp.wait(`location.pathname === ${JSON.stringify(path)} && document.body.innerText.includes('Log out')`);
  }
  async function logout() { await click('Log out'); await cdp.wait(`!!document.querySelector('#username')`); }
  async function openCase(id) {
    await cdp.navigate(`${base}/review/${id}`);
    await cdp.wait(`!!document.querySelector('#review-comment') && !document.body.innerText.includes('Loading current case and history…')`);
  }
  const detail = id => cdp.evaluate(`fetch('/api/backend/review/${id}').then(r => r.json())`);
  const history = id => cdp.evaluate(`fetch('/api/backend/review/${id}/history').then(r => r.json())`);
  async function seed(filename, original) {
    const id = await cdp.evaluate(`(async () => {
      const session = await (await fetch('/api/backend/sessions/current')).json();
      const bytes = await (await fetch('/api/backend/invoices/${original}/document')).blob();
      const form = new FormData(); form.append('file', new File([bytes], ${JSON.stringify(filename)}, {type:'image/png'}));
      const response = await fetch('/api/backend/invoices', {method:'POST', headers:{'X-CSRF-Token':session.csrf_token,'Idempotency-Key':crypto.randomUUID()},body:form});
      if(response.status!==202) throw new Error('Public seed upload failed');
      return (await response.json()).invoice_id;
    })()`);
    // Wait through the actual Intake poller: DOM wait conditions must be synchronous.
    await cdp.navigate(`${base}/invoices/${id}`);
    await cdp.wait(text('Held for Review'));
    return id;
  }

  stage = 'Intake upload and AP role boundary';
  await login('browser_owner');
  await cdp.navigate(base + '/invoices/new');
  await cdp.wait(`!!document.querySelector('#invoice-file')`);
  const dom = await cdp.send('DOM.getDocument');
  const file = await cdp.send('DOM.querySelector', { nodeId: dom.root.nodeId, selector: '#invoice-file' });
  await cdp.send('DOM.setFileInputFiles', { nodeId: file.nodeId, files: [png] });
  await click('Upload invoice');
  await cdp.wait(`Array.from(document.querySelectorAll('a[href]')).some(node => new RegExp('^/invoices/[0-9a-f-]{36}$').test(node.getAttribute('href')))`);
  const path = await cdp.evaluate(`Array.from(document.querySelectorAll('a[href]')).find(node => new RegExp('^/invoices/[0-9a-f-]{36}$').test(node.getAttribute('href'))).getAttribute('href')`);
  const first = path.split('/').at(-1);
  await cdp.navigate(base + path); await cdp.wait(text('Held for Review'));
  await cdp.navigate(base + '/review'); await cdp.wait(text('AP access required'));
  assert.equal(await cdp.evaluate(text('Correct extraction')), false);
  assert.equal(await cdp.evaluate(`fetch('/api/backend/review').then(r=>r.status)`), 403);
  await logout(); await login('browser_analyst');
  await cdp.navigate(base + '/review'); await cdp.wait(text('BROWSER-1'));
  await fill('#review-supplier', 'Browser Supplier'); await click('Apply review filters');
  await cdp.wait(text('1 matching cases')); await openCase(first);
  const comparison = await detail(first);
  const expectedReasons = comparison.comparison_rows.flatMap(row => row.findings
    .filter(finding => finding.evidence.outcome !== 'PASS').map(finding => finding.message));
  assert.ok(expectedReasons.length > 0, 'The case must contain a real line discrepancy');
  const renderedReasons = await cdp.evaluate(`Array.from(Array.from(document.querySelectorAll('section'))
    .find(section => section.querySelector('h2')?.textContent === 'Invoice / PO / GRN comparison')
    .querySelectorAll('table tbody td:last-child li')).map(node => node.textContent)`);
  assert.deepEqual(renderedReasons, expectedReasons, 'Each row must show its exact readable non-PASS reasons');
  stage = 'protected original frame contract';
  assert.equal(await cdp.evaluate(`document.querySelector('iframe[title="Original invoice document"]')?.getAttribute('src')`), `/api/backend/invoices/${first}/document`, 'Original iframe title and authenticated source remain unchanged');
  pass('real Intake upload, AP-only queue, supplier filtering, exact per-row reasons and private original');
  if (focus === 'comparison') { assert.deepEqual(errors, []); return; }

  stage = 'mandatory comment and duplicate-click approval';
  const beforePosts = requests.filter(r => r.path.endsWith('/decisions')).length;
  await click('Approve'); await cdp.wait(text('nonblank comment'));
  assert.equal(requests.filter(r => r.path.endsWith('/decisions')).length, beforePosts);
  await fill('#review-comment', 'Verified invoice and receipt evidence');
  await cdp.evaluate(`(() => { const button=Array.from(document.querySelectorAll('button')).find(node=>node.textContent==='Approve');button.click();button.click(); })()`);
  await cdp.wait(text('Payment authorized — not executed'));
  assert.equal(requests.filter(r => r.path.endsWith('/decisions')).length, beforePosts + 1);
  assert.equal((await detail(first)).revision, 1);
  pass('mandatory comment, one same-frame approval and audited authorization rather than payment execution');

  stage = 'correction, release and new evaluation';
  await click('Correct extraction');
  await fill('#line-0-unit_price', '10.000000'); await fill('#line-0-line_total', '20.000000');
  await fill('#edit-subtotal', '20.000000'); await fill('#edit-total_amount', '20.000000');
  await fill('#correction-comment', 'Correct unit price against the original');
  await click('Preview correction'); await cdp.wait(text('Proposed correction'));
  await click('Save correction'); await cdp.wait(text('Correction saved. Reevaluate'));
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('button')).find(node=>node.textContent==='Approve').disabled`), true);
  assert.ok((await history(first)).events.some(event => event.kind === 'approval_reversal'));
  await fill('#review-comment', 'Evaluate corrected extraction'); await click('Reevaluate');
  await cdp.wait(text('Reevaluation recorded.'));
  let current = await detail(first);
  assert.equal(current.revision, 3); assert.equal(current.evaluation.result.final_status, 'Auto-Pay');
  assert.equal(Number(current.comparison_rows[0].quantity_previously_approved), 0);
  assert.equal(Number(current.comparison_rows[0].quantity_available), 5);
  assert.equal(current.payment_blocked, false);
  assert.equal(current.operational_state, 'APPROVED');
  assert.equal(current.current_decision.actor_id, 'system:auto-pay');
  await cdp.wait(text('Current automatic decision'));
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('button')).find(node=>node.textContent==='Approve').disabled`), true);
  assert.equal((await history(first)).events.filter(event => event.kind === 'decision' && event.current).length, 1);
  pass('correction preview, audited quantity release, decision block, deterministic reevaluation and preserved history');

  stage = 'stale correction recovery';
  const competing = structuredClone(current.extraction); competing.lines[0].description = 'Competing correction';
  const external = await cdp.evaluate(`(async()=>{const s=await(await fetch('/api/backend/sessions/current')).json();return(await fetch('/api/backend/review/${first}',{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token,'If-Match':'"3"'},body:JSON.stringify({invoice:${JSON.stringify(competing)},comment:'Competing correction'})})).status})()`);
  assert.equal(external, 200);
  const before = await history(first);
  await click('Correct extraction'); await fill('#line-0-description', 'My stale correction');
  await fill('#correction-comment', 'Attempt stale correction'); await click('Preview correction');
  await cdp.wait(text('Proposed correction')); await click('Save correction'); await cdp.wait(text('Actions are paused.'));
  assert.deepEqual(await history(first), before);
  await click('Refresh case'); await cdp.wait(text('REEVALUATION REQUIRED'));
  assert.equal((await detail(first)).extraction.lines[0].description, 'Competing correction');
  pass('real If-Match conflict leaves audit unchanged and requires explicit refresh');

  stage = 'Analyst limit and Manager escalation';
  const managerCase = await seed('manager.png', first); await openCase(managerCase);
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('button')).find(node=>node.textContent==='Approve').disabled`), true);
  const bypass = await cdp.evaluate(`(async()=>{const s=await(await fetch('/api/backend/sessions/current')).json();return(await fetch('/api/backend/review/${managerCase}/decisions',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf_token,'If-Match':'"0"','X-Role':'ap_manager'},body:JSON.stringify({action:'approve',comment:'Attempt UI bypass'})})).status})()`);
  assert.equal(bypass, 403); assert.equal((await detail(managerCase)).revision, 0);
  await fill('#review-comment', 'Above USD limit; request Manager'); await click('Request Manager review'); await cdp.wait(text('Manager review requested'));
  await logout(); await login('browser_manager'); await openCase(managerCase);
  await fill('#review-comment', 'Manager verified above-limit evidence'); await click('Approve'); await cdp.wait(text('Payment authorized — not executed'));
  assert.equal((await detail(managerCase)).operational_state, 'APPROVED');
  pass('server-enforced Analyst limit despite forged header, audited escalation and Manager resolution');

  stage = 'automatic decision audit attribution';
  await cdp.navigate(`${base}/audit/${first}`);
  await cdp.wait(text('Automatic decision'));
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('article')).some(node =>
    node.querySelector('h3')?.textContent === 'Automatic decision' && node.innerText.includes('Automatic matching'))`), true);
  pass('automatic authorization remains explicitly system-attributed in historical Audit');

  stage = 'credit-note block, mobile and expired AP session';
  const note = await seed('note.png', first); await openCase(note);
  await fill('#review-comment', 'Request supplier credit note for discrepancy'); await click('Request credit note'); await cdp.wait(text('Credit Note Requested: payment remains blocked'));
  assert.equal((await detail(note)).evaluation.result.final_status, 'Held for Review');
  await fill('#review-comment', 'Refresh evidence without clearing pending credit note'); await click('Reevaluate'); await cdp.wait(text('Reevaluation recorded.'));
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('button')).find(node=>node.textContent==='Approve').disabled`), true);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.ok(await cdp.evaluate(`Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)<=390`));
  assert.equal(await cdp.evaluate(`Array.from(document.querySelectorAll('input,textarea,select')).every(node=>node.labels?.length>0)`), true);
  await cdp.evaluate(`document.querySelector('button').focus()`);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
  assert.equal(await cdp.evaluate(`document.activeElement!==document.body`), true);
  await cdp.evaluate(`(async()=>{const s=await(await fetch('/api/backend/sessions/current')).json();await fetch('/api/backend/sessions/current',{method:'DELETE',headers:{'X-CSRF-Token':s.csrf_token}})})()`);
  await click('Refresh case'); await cdp.wait(`location.pathname==='/login'`);
  assert.equal(await cdp.evaluate(`new URLSearchParams(location.search).get('returnTo')`), `/review/${note}`);
  await login('browser_analyst', { navigate: false, path: `/review/${note}` });
  await cdp.wait(`!!document.querySelector('#review-comment')`);
  assert.deepEqual(errors, []);
  pass('persistent credit-note payment block, mobile bounds/labels/keyboard and real 401 AP return path');
  console.log('AP browser acceptance complete; controlled extractor, no Azure or banking payment.');
}
try {
  await verify();
} catch (failure) {
  console.error(`FAIL ${stage}: ${failure?.name ?? 'error'} (private request/document values withheld)`);
  process.exitCode = 1;
} finally {
  clearTimeout(deadline); await cleanup();
}
