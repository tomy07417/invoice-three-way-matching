import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const { formatMoney, formatTimestamp } = await loadTypeScript('../lib/display-format.ts');

test('money localizes grouping and decimal separators without changing recorded precision', () => {
  assert.equal(formatMoney('1234567.2300', 'USD', 'en-US'), 'USD\u00a01,234,567.2300');
  assert.equal(formatMoney('1234567.2300', 'EUR', 'de-DE'), '1.234.567,2300\u00a0EUR');
});
test('money preserves large integers, small negatives, negative zero and fractional zeros', () => {
  assert.equal(formatMoney('900719925474099312345.000', 'USD', 'en-US'), 'USD\u00a0900,719,925,474,099,312,345.000');
  assert.equal(formatMoney('-0.00100', 'USD', 'en-US'), '-USD\u00a00.00100');
  assert.equal(formatMoney('-0.00', 'EUR', 'de-DE'), '-0,00\u00a0EUR');
  assert.equal(formatMoney('.5000', 'USD', 'en-US'), 'USD\u00a00.5000');
});
test('finite scientific notation expands exactly within the rendering budget', () => {
  assert.equal(formatMoney('1.2300e2', 'USD', 'en-US'), 'USD\u00a0123.00');
  assert.equal(formatMoney('-1.20e-3', 'EUR', 'de-DE'), '-0,00120\u00a0EUR');
  assert.equal(formatMoney('1e999', 'USD', 'en-US').includes('1e999'), false);
});
test('large scientific exponents remain exact and visible, never expanded or rounded', () => {
  for (const amount of ['1e1000', '1e-1001', '1e999999999999999999999999999', '-0.000e-999999999']) {
    assert.equal(formatMoney(amount, 'USD', 'en-US'), `${amount} USD (exact scientific notation)`);
  }
});
test('missing or invalid amount/currency is explicit and does not invent USD', () => {
  assert.equal(formatMoney(null, 'EUR', 'en-US'), 'Amount unknown · EUR');
  assert.equal(formatMoney('NaN', 'USD', 'en-US'), 'Amount unknown (NaN) · USD');
  assert.equal(formatMoney('12.300', null, 'en-US'), '12.300 · Currency unknown');
  assert.equal(formatMoney('12', 'not-a-currency', 'en-US'), '12 · Currency unknown (not-a-currency)');
  assert.equal(formatMoney(null, null, 'en-US'), 'Amount unknown · Currency unknown');
});
test('timestamps show an explicit timezone, honor locale and never substitute now', () => {
  assert.equal(formatTimestamp('2026-11-20T18:00:00Z', 'en-US', 'UTC'), 'Nov 20, 2026, 6:00:00 PM UTC');
  assert.equal(formatTimestamp('2026-11-20T18:00:00Z', 'de-DE', 'UTC'), '20. Nov. 2026, 18:00:00 UTC');
  assert.equal(formatTimestamp(null, 'en-US', 'UTC'), 'Time unknown');
  for (const value of ['invalid', '2026-11-20', '2026-02-30T12:00:00Z', '2026-11-20T18:00:00']) {
    assert.equal(formatTimestamp(value, 'en-US', 'UTC'), `Time unknown (${value})`);
  }
});
