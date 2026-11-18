import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const state = await loadTypeScript('../lib/review-state.ts');
const intake = await loadTypeScript('../lib/intake-state.ts');

function form() {
  const data = new FormData();
  for (const [key, value] of Object.entries({ supplier_name: 'Supplier', supplier_tax_id: 'tax', invoice_number: 'INV-1',
    invoice_date: '2026-10-02', due_date: '', po_references: 'PO-A, PO-B', currency: 'USD', subtotal: '9007199254740993.1200',
    tax_amount: '0.0000', total_amount: '9007199254740993.1200',
    'line-0-description': 'Item', 'line-0-quantity': '2.0000', 'line-0-unit_price': '1.20', 'line-0-line_total': '2.4000',
    'line-0-po_line_reference': '', 'line-0-po_number': 'PO-B', 'line-0-po_line_number': '3', 'line-0-unit_price_basis': 'net',
  })) data.set(key, value);
  return data;
}

test('correction forms preserve exact strings, explicit associations and nullable evidence', () => {
  const data = form();
  const draft = state.draftFromForm(data, 1);
  assert.equal(draft.total_amount, '9007199254740993.1200');
  assert.equal(draft.lines[0].quantity, '2.0000');
  assert.equal(draft.lines[0].po_number, 'PO-B');
  assert.equal(draft.lines[0].po_line_number, 3);
  assert.deepEqual(draft.po_references, ['PO-A', 'PO-B']);
  assert.equal(draft.due_date, null);
  data.set('po_references_unknown', 'on');
  data.set('line-0-unit_price_basis', 'unknown');
  data.set('line-0-po_line_number', '');
  const unknown = state.draftFromForm(data, 1);
  assert.equal(unknown.po_references, null);
  assert.equal(unknown.lines[0].po_line_number, null);
  assert.equal(unknown.lines[0].unit_price_basis, 'unknown');
  data.delete('po_references_unknown'); data.set('po_references', '');
  assert.deepEqual(state.draftFromForm(data, 1).po_references, []);
});

test('correction preserves accounting evidence but invalidates it after line reindexing', () => {
  const original = state.draftFromForm(form(), 1);
  original.accounting_evidence = { taxes_separately_stated: true, adjustments: [
    { kind: 'discount', stage: 'before_subtotal', amount: '-10.00', line_index: 0 }] };
  const preserved = state.draftFromForm(form(), 1, original, [0]);
  assert.deepEqual(preserved.accounting_evidence, original.accounting_evidence);
  preserved.accounting_evidence.adjustments[0].amount = '99';
  assert.equal(original.accounting_evidence.adjustments[0].amount, '-10.00');
  assert.equal(state.draftFromForm(form(), 1, original, [1]).accounting_evidence, null);
  assert.equal(state.draftFromForm(form(), 0, original, []).accounting_evidence, null);
});

test('invalid correction input and comments fail without coercing prices or line identities', () => {
  for (const invalid of ['NaN', 'Infinity', '2,00', 'abc']) {
    const data = form(); data.set('total_amount', invalid);
    assert.throws(() => state.draftFromForm(data, 1), /decimal/i);
  }
  const data = form(); data.set('line-0-po_line_number', '3.5');
  assert.throws(() => state.draftFromForm(data, 1), /line number/i);
  assert.throws(() => state.requiredReviewComment('  '), /comment/i);
  assert.equal(state.requiredReviewComment('  Verified original  '), 'Verified original');
});

test('change preview explains changed evidence without rewriting the original', () => {
  const original = state.draftFromForm(form(), 1);
  const before = JSON.stringify(original);
  const proposed = structuredClone(original); proposed.lines[0].quantity = '3.00'; proposed.currency = 'EUR';
  const changes = state.correctionChanges(original, proposed);
  assert.equal(changes.length, 2);
  assert.ok(changes.some(change => change.before === 'USD' && change.after === 'EUR'));
  assert.ok(changes.some(change => change.before === '2.0000' && change.after === '3.00'));
  assert.equal(JSON.stringify(original), before);
});

test('role controls, stale recovery and local AP return paths remain explicit', () => {
  assert.equal(state.isAP('intake'), false);
  assert.equal(state.isAP('ap_analyst'), true); assert.equal(state.isAP('ap_manager'), true);
  assert.match(state.reviewError({ status: 409, message: 'The case changed' }), /refresh/i);
  assert.match(state.reviewError({ status: 428 }), /refresh/i);
  assert.match(state.reviewError({ status: 401 }), /sign in/i);
  for (const path of ['/review', '/review/11111111-2222-4333-8444-555555555555']) assert.equal(intake.safeReturnPath(path), path);
  for (const path of ['/review.evil', '/review/%2f%2fattacker', '//attacker/review', '/review/../login']) assert.equal(intake.safeReturnPath(path), '/invoices');
});
