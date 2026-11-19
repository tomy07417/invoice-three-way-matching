import type { AuditEvent, AuditFilters, AuditKind, JsonValue } from './contracts';

export const AUDIT_LABELS: Record<AuditKind, string> = {
  creation: 'Invoice created', extraction: 'Extraction', evaluation: 'Automatic evaluation',
  correction: 'Extraction correction', reevaluation: 'Reevaluation', decision: 'Decision',
  escalation: 'Manager escalation', credit_note_request: 'Credit note request',
  state_change: 'Operational state change', approval_reversal: 'Audited approval reversal',
};
export function auditEventLabel(event: Pick<AuditEvent, 'kind' | 'actor'>): string {
  if (event.kind === 'decision') return event.actor.type === 'system' ? 'Automatic decision' : 'Human decision';
  return AUDIT_LABELS[event.kind];
}
export function auditValue(value: JsonValue | undefined, known = true): string {
  if (!known) return 'Not recorded';
  if (value === null || value === undefined) return 'Unknown';
  if (Array.isArray(value) && !value.length) return 'None';
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}
export function auditActor(actor: AuditEvent['actor']): string {
  if (actor.type === 'unknown') return 'Actor not recorded';
  if (actor.type === 'system' && !actor.name && !actor.id) return 'System action — identity not recorded';
  const identity = actor.name ?? (actor.id ? `Actor ${actor.id}` : 'Identity not recorded');
  return actor.role ? `${identity} · ${actor.role}` : identity;
}
export class AuditFilterError extends Error {
  constructor(public readonly field: string, message: string) { super(message); }
}
export function auditFiltersFromForm(form: FormData): AuditFilters {
  const text = (key: string) => String(form.get(key) ?? '').trim();
  const kind = text('kind');
  if (kind && !(kind in AUDIT_LABELS)) throw new AuditFilterError('kind', 'Choose a known event kind.');
  const actor = text('actor_id');
  if (actor.length > 128) throw new AuditFilterError('actor_id', 'Actor ID must be at most 128 characters.');
  const rawLimit = text('limit') || '50';
  if (!/^\d+$/.test(rawLimit) || Number(rawLimit) < 1 || Number(rawLimit) > 100) {
    throw new AuditFilterError('limit', 'Choose between 1 and 100 events per page.');
  }
  const timestamp = (key: string) => {
    if (!text(key)) return undefined;
    const date = new Date(text(key));
    if (!Number.isFinite(date.getTime())) throw new AuditFilterError(key, 'Enter a valid local date and time.');
    return date.toISOString();
  };
  const from = timestamp('time_from'); const to = timestamp('time_to');
  if (from && to && from > to) throw new AuditFilterError('time_to', 'End time must not be before start time.');
  return { offset: 0, limit: Number(rawLimit), kind: kind as AuditKind || undefined,
    actor_id: actor || undefined, time_from: from, time_to: to };
}
