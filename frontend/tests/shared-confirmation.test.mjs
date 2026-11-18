import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';

test('module import is SSR safe and calling without a browser fails closed', async () => {
  assert.equal(typeof globalThis.window, 'undefined');
  const { confirmAction } = await loadTypeScript('../components/shared/confirmation.ts');
  assert.equal(confirmAction('Confirm?'), false);
});
test('native browser modal returns cancellation and acceptance with the exact message', async () => {
  const { confirmAction } = await loadTypeScript('../components/shared/confirmation.ts');
  const messages = [];
  try {
    globalThis.window = { confirm(message) { messages.push(message); return false; } };
    assert.equal(confirmAction('Reject invoice?'), false);
    globalThis.window.confirm = message => { messages.push(message); return true; };
    assert.equal(confirmAction('Approve invoice?'), true);
    assert.deepEqual(messages, ['Reject invoice?', 'Approve invoice?']);
  } finally { delete globalThis.window; }
});
