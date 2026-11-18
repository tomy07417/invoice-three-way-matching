import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const state = await loadTypeScript('../lib/review-state.ts');

test('rejection confirmation states the decision and preserves original automatic evidence', () => {
  assert.match(state.decisionConfirmation('reject'), /^Reject invoice/);
  assert.match(state.decisionConfirmation('reject'), /automatic outcome remains unchanged/);
});
test('credit-note confirmation explains payment blocking without claiming a banking operation', () => {
  assert.match(state.decisionConfirmation('request_credit_note'), /^Request credit note/);
  assert.match(state.decisionConfirmation('request_credit_note'), /block payment authorization/);
});
test('existing approval confirmation still explicitly rules out bank payment', () => {
  assert.equal(state.decisionConfirmation('approve'), 'Confirm payment authorization against the current evidence? No bank payment will be executed.');
});
