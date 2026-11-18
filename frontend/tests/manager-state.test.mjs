import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const state = await loadTypeScript('../lib/manager-state.ts');

const current = { price_tolerance: '0.02', analyst_approval_limit: '5000' };
test('percent conversion is exact, handles zero, scientific notation and unbounded magnitudes without expansion', () => {
  for (const [fraction, percent] of [['0.02', '2'], ['0', '0'], ['1.234567890123456789', '123.4567890123456789'], ['9e999999999999999999', '9e1000000000000000001'], ['1e-999999999999999999', '1e-999999999999999997']]) {
    assert.equal(state.fractionToPercent(fraction), percent);
    assert.ok(state.decimalEqual(state.percentToFraction(percent), fraction));
  }
  assert.equal(state.percentToFraction('2e2'), '2');
  assert.equal(state.fractionToPercent('-0.000e999'), '0');
  assert.ok(state.decimalEqual('-0', '0e-20'));
  assert.ok(state.decimalEqual('5000.00', '5e3'));
  assert.equal(state.decimalEqual('0.020000000000000001', '0.02'), false);
});
test('form rejects negative, nonfinite and malformed decimals inline, without changing input', () => {
  for (const invalid of ['-1', '-1e-999999', 'NaN', 'Infinity', '', '1,2', '1e', '--0']) {
    const draft = { tolerancePercent: invalid, analystLimit: invalid, comment: '' };
    const before = structuredClone(draft);
    const result = state.validateParameters(draft, current);
    assert.ok(result.errors.tolerancePercent); assert.ok(result.errors.analystLimit);
    assert.equal(result.values, null); assert.deepEqual(draft, before);
  }
});
test('partial updates have no caps, optional comments and negative-zero/no-change equality', () => {
  assert.deepEqual(state.validateParameters({ tolerancePercent: '2.000', analystLimit: '5e3', comment: 'not an audit event' }, current), { errors: {}, values: {}, changed: false });
  const result = state.validateParameters({ tolerancePercent: '9e99999', analystLimit: '9007199254740993.123456789', comment: '  rationale  ' }, current);
  assert.deepEqual(result, { errors: {}, values: { price_tolerance: '9e99997', analyst_approval_limit: '9007199254740993.123456789', comment: 'rationale' }, changed: true });
  assert.deepEqual(state.validateParameters({ tolerancePercent: '-0', analystLimit: '-0e100', comment: '' }, { price_tolerance: '0', analyst_approval_limit: '0' }).values, {});
  assert.deepEqual(state.validateParameters({ tolerancePercent: '3', analystLimit: '5000', comment: '' }, current).values, { price_tolerance: '0.03' });
});
test('conflicts and uncertain mutations require explicit refresh; definite validation failures do not', () => {
  for (const failure of [{ status: 409 }, { status: 428 }, { status: 0 }, { status: 500 }, { status: 200, code: 'invalid_response' }, new Error('unknown')]) assert.equal(state.requiresParameterRefresh(failure), true);
  for (const status of [401, 403, 413, 422, 429]) assert.equal(state.requiresParameterRefresh({ status }), false);
});
test('selected escalation comments use recorded provenance without fabricating an Analyst', () => {
  const events = [ { kind: 'escalation', actor_role: 'ap_analyst', comment: 'Analyst evidence', at: 'first' }, { kind: 'decision', actor_role: 'ap_analyst', comment: 'unrelated' }, { kind: 'escalation', actor_role: 'ap_manager', comment: 'Manager evidence' } ];
  assert.deepEqual(state.escalationComments(events), [events[0], events[2]]);
  assert.deepEqual(state.escalationComments([]), []);
});
