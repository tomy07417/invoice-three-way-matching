import test from 'node:test';
import assert from 'node:assert/strict';
import { loadTypeScript } from './load-typescript.mjs';
const state = await loadTypeScript('../lib/audit-state.ts');

test('audit differences distinguish unrecorded evidence, recorded null and exact values', () => {
  assert.equal(state.auditValue('9007199254740993.000001'), '9007199254740993.000001');
  assert.equal(state.auditValue(null), 'Unknown');
  assert.equal(state.auditValue(null, false), 'Not recorded');
  assert.equal(state.auditValue([]), 'None');
  assert.equal(state.auditValue(false), 'false');
});

test('actor labels never invent an identity or convert missing metadata into system proof', () => {
  assert.equal(state.auditActor({ id: null, name: null, role: null, type: 'unknown' }), 'Actor not recorded');
  assert.match(state.auditActor({ id: null, name: null, role: null, type: 'system' }), /identity not recorded/);
  assert.equal(state.auditActor({ id: '42', name: 'Manager', role: 'ap_manager', type: 'recorded' }), 'Manager · ap_manager');
});

test('audit filters retain inclusive offset timestamps and reject invalid fields without mutations', () => {
  const form = new FormData(); form.set('kind', 'correction'); form.set('actor_id', '42'); form.set('limit', '50');
  assert.deepEqual(state.auditFiltersFromForm(form), { offset: 0, limit: 50, kind: 'correction', actor_id: '42', time_from: undefined, time_to: undefined });
  form.set('time_from', '2026-11-18T10:00:00+03:00'); form.set('time_to', '2026-11-18T10:00:00+03:00');
  assert.equal(state.auditFiltersFromForm(form).time_from, '2026-11-18T07:00:00.000Z');
  form.set('time_to', '2026-11-17T10:00:00+03:00');
  assert.throws(() => state.auditFiltersFromForm(form), error => error.field === 'time_to');
  form.set('time_to', 'invalid');
  assert.throws(() => state.auditFiltersFromForm(form), error => error.field === 'time_to');
  form.delete('time_to'); form.set('kind', 'invented');
  assert.throws(() => state.auditFiltersFromForm(form), error => error.field === 'kind');
});
