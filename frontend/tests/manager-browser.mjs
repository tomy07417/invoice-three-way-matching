// Real Chrome + Next + FastAPI + isolated PostgreSQL; controlled extraction, never Azure.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { launchChrome, stopChild } from './cdp.mjs';
const base = process.env.INTAKE_BASE_URL ?? 'http://127.0.0.1:3212';
assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
assert.ok(process.env.TEST_DATABASE_URL, 'Explicit isolated test_ PostgreSQL URL required');
const root = fileURLToPath(new URL('../../', import.meta.url));
const backend = spawn(`${root}backend/.venv/bin/python`, [fileURLToPath(new URL('./intake-backend.py', import.meta.url))], {
  cwd: root, env: { PATH: '/usr/bin:/bin', PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1',
    HOME: process.env.HOME, TMPDIR: process.env.TMPDIR,
    TEST_DATABASE_URL: process.env.TEST_DATABASE_URL, AP_REVIEW_FIXTURE: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
});
let chrome; let stage = 'fixture readiness'; let closing;
const cleanup = () => closing ??= (async () => { try { await chrome?.close(); } finally { await stopChild(backend); } })();
const deadline = setTimeout(() => { console.error(`FAIL timeout at ${stage}`); void cleanup().finally(() => process.exit(1)); }, 180000);
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void cleanup().finally(() => process.exit(1)); });
async function verify() {
  const png = await new Promise((resolve, reject) => {
    let output = ''; const timer = setTimeout(() => reject(new Error('Fixture readiness timeout')), 25000);
    const inspect = chunk => {
      output = (output + chunk.toString()).slice(-16000);
      const match = output.match(/INTAKE_FIXTURE (\{[^\n]+\})/);
      if (match && output.includes('Uvicorn running on http://127.0.0.1:3211')) { clearTimeout(timer); resolve(JSON.parse(match[1]).png); }
    };
    backend.stdout.on('data', inspect); backend.stderr.on('data', inspect);
    backend.once('error', () => { clearTimeout(timer); reject(new Error('Fixture launch failed')); });
    backend.once('exit', () => { clearTimeout(timer); reject(new Error('Fixture exited')); });
  });
  chrome = await launchChrome(); const cdp = chrome.client;
  const errors = []; const requests = []; const dialogs = [];
  let acceptNextDialog = true;
  cdp.on('Runtime.exceptionThrown', () => errors.push('Unhandled browser exception'));
  cdp.on('Network.requestWillBeSent', ({ request }) => {
    if (new URL(request.url).pathname.startsWith('/api/backend/')) requests.push({ path: new URL(request.url).pathname, method: request.method });
  });
  cdp.on('Page.javascriptDialogOpening', ({ message }) => {
    const known = /^(Save rule parameters|Refresh replaces|Confirm payment authorization|Reject invoice|Request credit note)/.test(message);
    dialogs.push(message.split('\n')[0]); if (!known) errors.push('Unexpected confirmation');
    void cdp.send('Page.handleJavaScriptDialog', { accept: known && acceptNextDialog });
    acceptNextDialog = true;
  });
  const text = value => `document.body.innerText.includes(${JSON.stringify(value)})`;
  const click = label => cdp.evaluate(`Array.from(document.querySelectorAll('button,a')).find(n=>n.textContent.trim()===${JSON.stringify(label)})?.click()`);
  async function fill(selector, value) {
    await cdp.evaluate(`(() => {const n=document.querySelector(${JSON.stringify(selector)});n.focus();n.select();})()`);
    await cdp.send('Input.insertText', { text: value });
  }
  async function login(username) {
    await cdp.navigate(base + '/login'); await cdp.wait(`!!document.querySelector('#username')`);
    await fill('#username', username); await fill('#password', 'browser test password');
    await cdp.evaluate(`document.querySelector('form').requestSubmit()`); await cdp.wait(text('Log out'));
  }
  async function logout() { await click('Log out'); await cdp.wait(`!!document.querySelector('#username')`); }
  const get = path => cdp.evaluate(`fetch('/api/backend${path}').then(async r=>({status:r.status,data:await r.json()}))`);
  const changeParameters = (version, values) => cdp.evaluate(`(async()=>{
    const session=await(await fetch('/api/backend/sessions/current')).json();
    const r=await fetch('/api/backend/manager/parameters',{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token,'If-Match':${JSON.stringify('"' + version + '"')}},body:JSON.stringify(${JSON.stringify(values)})});
    return {status:r.status,data:await r.json()};})()`);
  const pass = label => console.log('PASS ' + label);

  stage = 'Intake upload and negative Manager permissions';
  await login('browser_owner'); await cdp.navigate(base + '/invoices/new'); await cdp.wait(`!!document.querySelector('#invoice-file')`);
  const dom = await cdp.send('DOM.getDocument');
  const file = await cdp.send('DOM.querySelector', { nodeId: dom.root.nodeId, selector: '#invoice-file' });
  await cdp.send('DOM.setFileInputFiles', { nodeId: file.nodeId, files: [png] }); await click('Upload invoice');
  stage = 'uploaded invoice navigation';
  await cdp.wait(`Array.from(document.querySelectorAll('a[href]')).some(n=>new RegExp('^/invoices/[0-9a-f-]{36}$').test(n.getAttribute('href')))`);
  const first = await cdp.evaluate(`Array.from(document.querySelectorAll('a[href]')).find(n=>new RegExp('^/invoices/[0-9a-f-]{36}$').test(n.getAttribute('href'))).getAttribute('href').split('/').at(-1)`);
  await cdp.navigate(base + '/invoices/' + first); await cdp.wait(text('Held for Review'));
  const managerCase = await cdp.evaluate(`(async()=>{const s=await(await fetch('/api/backend/sessions/current')).json();const blob=await(await fetch('/api/backend/invoices/${first}/document')).blob();
    const form=new FormData();form.append('file',new File([blob],'manager.png',{type:'image/png'}));
    const r=await fetch('/api/backend/invoices',{method:'POST',headers:{'X-CSRF-Token':s.csrf_token,'Idempotency-Key':crypto.randomUUID()},body:form});if(r.status!==202)throw Error('Upload failed');return(await r.json()).invoice_id;})()`);
  await cdp.navigate(base + '/invoices/' + managerCase); await cdp.wait(text('Held for Review'));
  for (const route of ['/manager/parameters', '/audit/' + first]) {
    await cdp.navigate(base + route); await cdp.wait(text('Manager access required'));
    assert.equal(await cdp.evaluate(`!!document.querySelector('#manager-tolerance')`), false);
  }
  for (const route of ['/manager/parameters', '/manager/parameters/history', '/audit/' + first]) assert.equal((await get(route)).status, 403);
  pass('Intake actual upload/evaluation and Manager API/UI denial');

  stage = 'Analyst escalation and negative administration';
  await logout(); await login('browser_analyst');
  assert.equal((await get('/manager/parameters')).status, 403); assert.equal((await get('/audit/' + first)).status, 403);
  await cdp.navigate(base + '/review/' + managerCase); await cdp.wait(`!!document.querySelector('#review-comment')`);
  await fill('#review-comment', 'Analyst asks Manager to review the recorded price exception.'); await click('Request Manager review');
  await cdp.wait(text('ESCALATED'));
  pass('Analyst escalation uses public revision/CSRF and administration remains denied');

  stage = 'Manager current parameters and invalid draft';
  await logout(); await login('browser_manager'); await cdp.navigate(base + '/manager/parameters');
  await cdp.wait(`!!document.querySelector('#manager-tolerance:not(:disabled)')`);
  const original = (await get('/manager/parameters')).data; const initialHistory = (await get('/manager/parameters/history')).data;
  assert.equal(original.price_tolerance, '0.02'); assert.equal(original.analyst_approval_limit, '5000');
  const beforeInvalid = requests.filter(r=>r.method==='PATCH').length;
  await fill('#manager-tolerance', '-1'); await cdp.evaluate(`document.querySelector('#manager-tolerance').closest('form').requestSubmit()`);
  await cdp.wait(`document.querySelector('#manager-tolerance').getAttribute('aria-invalid')==='true'`);
  assert.equal(requests.filter(r=>r.method==='PATCH').length, beforeInvalid);
  const invalid = await changeParameters(original.version_id, { price_tolerance: '-1' }); assert.equal(invalid.status, 422);
  assert.deepEqual((await get('/manager/parameters')).data, original);
  assert.equal((await get('/manager/parameters/history')).data.total, initialHistory.total);
  pass('Negative draft stays inline; public invalid update changes neither current version nor history');

  stage = 'Exact parameter update and no-op audit';
  await fill('#manager-tolerance', '3'); await fill('#manager-limit', '17.123456789012345678901');
  await fill('#manager-comment', 'Controlled future-only demonstration.');
  await cdp.evaluate(`document.querySelector('#manager-tolerance').closest('form').requestSubmit()`); await cdp.wait(text('Saved version'));
  const updated = (await get('/manager/parameters')).data;
  assert.equal(updated.price_tolerance, '0.03'); assert.equal(updated.analyst_approval_limit, '17.123456789012345678901');
  assert.equal(updated.version_id, original.version_id + 1);
  const recorded = (await get('/manager/parameters/history')).data;
  assert.equal(recorded.total, initialHistory.total + 1); assert.equal(recorded.items[0].comment, 'Controlled future-only demonstration.');
  await cdp.evaluate(`document.querySelector('#manager-tolerance').closest('form').requestSubmit()`); await cdp.wait(text('No change:'));
  assert.equal((await get('/manager/parameters/history')).data.total, recorded.total);
  pass('Exact-decimal save, future-only explanation, confirmation, history and unchanged CAS/no audit');

  stage = 'Concurrent stale update and retained draft';
  assert.equal((await changeParameters(updated.version_id, { price_tolerance: '0.04' })).status, 200);
  await fill('#manager-tolerance', '5'); await cdp.evaluate(`document.querySelector('#manager-tolerance').closest('form').requestSubmit()`);
  await cdp.wait(text('Refresh'));
  await cdp.wait(`document.querySelector('#manager-tolerance').disabled`);
  assert.equal(await cdp.evaluate(`document.querySelector('#manager-tolerance').value`), '5');
  assert.equal((await get('/manager/parameters')).data.price_tolerance, '0.04');
  // The stale attempt must not append another history row.
  assert.equal((await get('/manager/parameters/history')).data.total, recorded.total + 1);
  await click('Refresh current parameters'); await cdp.wait(`document.querySelector('#manager-tolerance').value==='4' && !document.querySelector('#manager-tolerance').disabled`);
  pass('Stale version cannot overwrite or append audit; explicit refresh retains draft until accepted');

  stage = 'Manager escalation evidence and approval';
  await cdp.navigate(base + '/manager/escalations'); await cdp.wait(text('BROWSER-2'));
  await click('View comments: BROWSER-2'); await cdp.wait(text('Analyst asks Manager to review the recorded price exception.'));
  await cdp.navigate(base + '/review/' + managerCase); await cdp.wait(`!!document.querySelector('#review-comment')`);
  const beforeEmptyComment = (await get('/audit/' + managerCase)).data;
  const writesBeforeEmpty = requests.filter(request => request.method === 'POST').length;
  await click('Approve'); await cdp.wait(text('A nonblank comment'));
  assert.equal(await cdp.evaluate(`document.querySelector('#review-comment').getAttribute('aria-invalid')`), 'true', 'Required comment error must be attached inline');
  assert.equal(await cdp.evaluate(`document.activeElement.id`), 'review-comment', 'Invalid comment receives keyboard focus');
  assert.equal(requests.filter(request => request.method === 'POST').length, writesBeforeEmpty);
  const afterEmptyComment = (await get('/audit/' + managerCase)).data;
  assert.equal(afterEmptyComment.revision, beforeEmptyComment.revision); assert.equal(afterEmptyComment.total, beforeEmptyComment.total);
  await fill('#review-comment', 'Manager authorizes the isolated demo exception.'); await click('Approve'); await cdp.wait(text('APPROVED'));
  pass('Manager sees Analyst provenance and approves above evaluated limit with confirmation');

  stage = 'Audit chronology, filters, provenance and exact differences';
  await cdp.navigate(base + '/audit/' + managerCase); await cdp.wait(text('Manager authorizes the isolated demo exception.'));
  assert.equal(await cdp.evaluate(text('Derived projection')), true);
  const fullAudit = (await get('/audit/' + managerCase)).data;
  const changed = fullAudit.events.find(event => event.diffs.some(diff => typeof diff.before === 'string' && typeof diff.after === 'string'));
  assert.ok(changed, 'The real case must have a recorded state difference');
  const rows = await cdp.evaluate(`Array.from(document.getElementById(${JSON.stringify('audit-event-' + changed.id)}).closest('article').querySelectorAll('tbody tr')).map(row=>Array.from(row.cells).map(cell=>cell.innerText))`);
  for (const diff of changed.diffs.filter(diff => typeof diff.before === 'string' && typeof diff.after === 'string')) {
    assert.deepEqual(rows.find(row => row[0] === diff.field)?.slice(1, 3), [diff.before, diff.after]);
  }
  assert.equal(await cdp.evaluate(`!!document.querySelector('a[href="/review/${managerCase}"]')`), true);
  await cdp.evaluate(`document.querySelector('[name="kind"]').value='decision'; document.querySelector('[aria-label="Audit filters"]').requestSubmit()`);
  await cdp.wait(`document.querySelectorAll('.audit-timeline > li').length===1`);
  const audit = (await get('/audit/' + managerCase + '?kind=decision')).data;
  assert.equal(audit.events.length, 1); assert.equal(audit.events[0].actor.type, 'recorded');
  assert.equal(audit.events[0].comment, 'Manager authorizes the isolated demo exception.');
  await click('Clear filters'); await cdp.wait(text('Manager escalation'));
  pass('Manager audit renders truthful chronology/diffs, recorded actors and server-filtered decision');

  stage = 'Manager rejection confirmation';
  await cdp.navigate(base + '/review/' + first); await cdp.wait(`!!document.querySelector('#review-comment')`);
  await fill('#review-comment', 'Manager rejects this isolated demo invoice.');
  const confirmationCount = dialogs.length;
  const beforeCancel = (await get('/audit/' + first)).data;
  const writesBeforeCancel = requests.filter(request => request.method === 'POST').length;
  acceptNextDialog = false; await click('Reject invoice');
  assert.equal(dialogs.length, confirmationCount + 1, 'Manager rejection must require confirmation');
  assert.equal(requests.filter(request => request.method === 'POST').length, writesBeforeCancel, 'Cancelled confirmation must not send a mutation');
  const afterCancel = (await get('/audit/' + first)).data;
  assert.equal(afterCancel.revision, beforeCancel.revision); assert.equal(afterCancel.total, beforeCancel.total);
  assert.equal(await cdp.evaluate(`document.querySelector('#review-comment').value`), 'Manager rejects this isolated demo invoice.');
  await click('Reject invoice'); await cdp.wait(text('REJECTED'));
  assert.equal(dialogs.length, confirmationCount + 2);
  pass('Manager rejection requires a recorded comment and explicit confirmation');

  stage = 'Manager and audit mobile labels, contrast and keyboard focus';
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  const luminance = color => {
    const channels = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => value / 255)
      .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  for (const [route, ready] of [['/manager/parameters', `!!document.querySelector('#manager-tolerance:not(:disabled)')`],
    ['/audit/' + managerCase, text('matching events')]]) {
    await cdp.navigate(base + route); await cdp.wait(ready);
    assert.equal(await cdp.evaluate(`document.documentElement.scrollWidth <= innerWidth + 2`), true, 'No page-level mobile overflow');
    assert.deepEqual(await cdp.evaluate(`Array.from(document.querySelectorAll('input,select,textarea')).filter(n=>n.type!=='hidden' && !n.labels?.length && !n.getAttribute('aria-label')).map(n=>n.id||n.name)`), []);
    await cdp.evaluate(`document.querySelector('input,select,textarea').focus()`);
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 });
    assert.equal(await cdp.evaluate(`!['BODY','HTML'].includes(document.activeElement.tagName) && getComputedStyle(document.activeElement).outlineStyle !== 'none'`), true);
    const colors = await cdp.evaluate(`(() => {const heading=document.querySelector('h1');let parent=heading;let bg;while(parent){bg=getComputedStyle(parent).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)' && bg!=='transparent')break;parent=parent.parentElement;}return [getComputedStyle(heading).color,parent?bg:'rgb(255, 255, 255)'];})()`);
    const values = colors.map(luminance).sort((a, b) => a - b);
    assert.ok((values[1] + 0.05) / (values[0] + 0.05) >= 4.5, 'Sampled heading text contrast meets 4.5:1');
  }
  await cdp.send('Emulation.clearDeviceMetricsOverride');
  pass('Manager/audit mobile bounds, labeled controls, visible keyboard focus and sampled heading contrast');

  stage = 'Controlled provider 429 with actual durable retry state';
  await logout(); await login('browser_owner');
  const limited = await cdp.evaluate(`(async()=>{const s=await(await fetch('/api/backend/sessions/current')).json();
    const blob=await(await fetch('/api/backend/invoices/${first}/document')).blob();const form=new FormData();
    form.append('file',new File([blob],'rate-limited.png',{type:'image/png'}));const r=await fetch('/api/backend/invoices',{method:'POST',headers:{'X-CSRF-Token':s.csrf_token,'Idempotency-Key':crypto.randomUUID()},body:form});
    if(r.status!==202)throw Error('Controlled rate-limit upload failed');return(await r.json()).invoice_id;})()`);
  await cdp.navigate(base + '/invoices/' + limited); await cdp.wait(text('RETRY_WAIT'));
  const pending = (await get('/invoices/' + limited + '/processing')).data;
  assert.equal(pending.attempts, 1); assert.equal(pending.error.code, 'rate_limited'); assert.equal(pending.error.retryable, true);
  assert.equal(pending.attempt_history.length, 1); assert.equal(pending.attempt_history[0].reported_tokens, null);
  assert.ok(Date.parse(pending.next_eligible_at) >= Date.parse(pending.attempt_history[0].started_at) + 120000);
  const incomplete = (await get('/invoices/' + limited)).data;
  assert.equal(incomplete.automatic_outcome, null); assert.equal(incomplete.evaluation, null); assert.equal(incomplete.extraction, null);
  assert.deepEqual(incomplete.findings, []);
  await click('Confirm scheduled retry'); await cdp.wait(text('Already-scheduled work confirmed:'));
  assert.deepEqual((await get('/invoices/' + limited + '/processing')).data, pending, 'Confirming retry must not reset attempts, eligibility, events or errors');
  assert.deepEqual(errors, []);
  pass('Simulated provider 429 produces real RETRY_WAIT, no outcome/evaluation and no reset of retry evidence');
  console.log('PASS Manager/audit browser acceptance (controlled provider, no Azure)');
}
try { await verify(); } catch (error) { console.error(`FAIL ${stage}: ${error.message}`); process.exitCode = 1; }
finally { clearTimeout(deadline); await cleanup(); }
