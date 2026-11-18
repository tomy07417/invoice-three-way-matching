import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const state = await loadTypeScript('../lib/intake-state.ts');

test('local return paths cannot escape intake routes', () => {
  for (const input of ['https://evil.test', '//evil.test', '/\\evil', '/login', '/invoices.evil', '/invoices/%2f%2fevil', null]) {
    assert.equal(state.safeReturnPath(input), '/invoices');
  }
  for (const input of ['/invoices', '/invoices/new', '/invoices/12345678-1234-1234-1234-123456789abc']) {
    assert.equal(state.safeReturnPath(input), input);
  }
});
test('file validation preserves exact allowed media and size boundaries', () => {
  const types = ['application/pdf', 'image/png', 'image/jpeg'];
  for (const type of types) {
    assert.equal(state.validateFile({ name: 'invoice', type, size: 8388608 }, 8388608, types), null);
    assert.match(state.validateFile({ name: 'invoice', type, size: 8388609 }, 8388608, types), /8 MiB/);
  }
  assert.match(state.validateFile({ type: 'text/plain', size: 2 }, 8388608, types), /PDF, PNG or JPG/);
  assert.match(state.validateFile({ type: 'application/pdf', size: 0 }, 8388608, types), /empty/);
});
test('technical states never become business rejection; nullable amounts stay exact', () => {
  for (const status of ['QUEUED', 'EXTRACTING', 'EXTRACTED', 'EVALUATING', 'RETRY_WAIT']) assert.equal(state.isActive(status), true);
  for (const status of ['COMPLETED', 'EXTRACTION_FAILED', 'PROCESSING_FAILED', 'EVALUATION_INCOMPLETE']) assert.equal(state.isActive(status), false);
  assert.equal(state.displayValue(null), '—');
  assert.equal(state.displayValue('9007199254740993.123456'), '9007199254740993.123456');
  assert.equal(state.outcomeLabel('EVALUATION_INCOMPLETE', null), 'Unknown — evaluation incomplete');
  assert.equal(state.outcomeLabel('PROCESSING_FAILED', null), '—');
  for (const status of ['EXTRACTION_FAILED', 'PROCESSING_FAILED']) {
    assert.equal(state.outcomeLabel(status, 'Rejected'), '—');
  }
  assert.equal(state.outcomeLabel('COMPLETED', 'Rejected'), 'Rejected');
});
test('polling has bounded backoff, three-error pause and two-minute boundary', () => {
  assert.deepEqual(state.pollDecision(0, 0, true), { delay: 3000, reason: null });
  assert.deepEqual(state.pollDecision(1, 100, true), { delay: 6000, reason: null });
  assert.deepEqual(state.pollDecision(2, 100, true), { delay: 12000, reason: null });
  assert.equal(state.pollDecision(3, 100, true).reason, 'errors');
  assert.equal(state.pollDecision(0, 119999, true).reason, null);
  assert.equal(state.pollDecision(0, 120000, true).reason, 'timeout');
  assert.equal(state.pollDecision(0, 0, false).reason, 'terminal');
});
test('named HTTP and network errors have recoverable human explanations', () => {
  for (const status of [0, 401, 403, 413, 415, 422, 429, 500, 503]) assert.ok(state.errorMessage({ status }).length > 15);
  assert.match(state.errorMessage({ status: 401 }), /session/i);
  assert.match(state.errorMessage({ status: 403 }), /access/i);
  assert.match(state.errorMessage({ status: 0 }), /same file/i);
  assert.equal(state.isCancelled({ name: 'AbortError' }), true);
  assert.equal(state.isCancelled(new Error('bad')), false);
});
