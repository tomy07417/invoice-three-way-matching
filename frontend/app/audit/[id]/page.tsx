'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { AuditEvent, AuditFilters, AuditHistory } from '../../../lib/contracts';
import { AUDIT_LABELS, AuditFilterError, auditActor, auditEventLabel, auditFiltersFromForm, auditValue } from '../../../lib/audit-state';
import { reviewError } from '../../../lib/review-state';
import { useSession } from '../../../components/intake/session-gate';
import { TimestampValue } from '../../../components/shared/format-value';
import { FilterForm, TableRegion, Timeline } from '../../../components/shared/workspace';

function EventRecord({ event }: { event: AuditEvent }) {
  return <li><article aria-labelledby={`audit-event-${event.id}`}>
    <h3 id={`audit-event-${event.id}`}>{auditEventLabel(event)}</h3>
    <p><TimestampValue value={event.at} /> · {auditActor(event.actor)}</p>
    <p>{event.current ? 'Current version or active decision' : 'Historical / event record'}
      {event.revision !== null && ` · Case revision ${event.revision}`}
      {event.extraction_version !== null && ` · Extraction ${event.extraction_version}`}
      {event.evaluation_id !== null && ` · Evaluation ${event.evaluation_id}`}</p>
    {event.comment !== null && <p><strong>Recorded comment: </strong>{event.comment}</p>}
    <p className="audit-provenance">{event.provenance.derived ? 'Derived projection' : 'Recorded evidence'} · Source: {event.provenance.source} / {event.provenance.source_id}
      {event.provenance.note && ` · ${event.provenance.note}`}</p>
    {event.diffs.length > 0 && <TableRegion label="Recorded field differences"><table><caption>Recorded field differences</caption>
      <thead><tr><th>Field</th><th>Before</th><th>After</th><th>Provenance</th></tr></thead>
      <tbody>{event.diffs.map((diff, index) => <tr key={`${diff.field}-${index}`}><th scope="row">{diff.field}</th>
        <td><pre>{auditValue(diff.before, diff.before_known)}</pre></td><td><pre>{auditValue(diff.after)}</pre></td>
        <td>{diff.derived ? 'Derived comparison' : 'Recorded change'} · {diff.source}</td>
      </tr>)}</tbody></table></TableRegion>}
    <details><summary>Recorded evidence and source identifiers</summary><pre>{JSON.stringify(event.evidence, null, 2)}</pre>
      <p>Event ID: {event.id} · Actor ID: {event.actor.id ?? 'Not recorded'} · Actor provenance: {event.actor.type}</p></details>
  </article></li>;
}

function AuditCase({ id }: { id: string }) {
  const { handleError } = useSession();
  const [filters, setFilters] = useState<AuditFilters>({ offset: 0, limit: 50 });
  const [generation, setGeneration] = useState(0);
  const [data, setData] = useState<AuditHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<AuditFilterError | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null); setData(null);
    void api.auditHistory(id, filters, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(failure => {
      if (controller.signal.aborted || failure instanceof DOMException && failure.name === 'AbortError') return;
      handleError(failure); setError(reviewError(failure));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, filters, generation, handleError]);
  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try { const next = auditFiltersFromForm(new FormData(event.currentTarget)); setFieldError(null); setFilters(next); }
    catch (failure) {
      if (failure instanceof AuditFilterError) {
        setFieldError(failure);
        event.currentTarget.querySelector<HTMLElement>(`[name="${failure.field}"]`)?.focus();
      } else setError('Check the audit filters.');
    }
  }
  function validation(name: string) {
    return { 'aria-invalid': fieldError?.field === name || undefined,
      'aria-describedby': fieldError?.field === name ? 'audit-filter-error' : undefined };
  }
  return <main className="audit-workspace"><h1>Invoice audit timeline</h1><p>Case: {id}</p>
    <nav aria-label="Audit case navigation"><Link href="/audit">Choose another case</Link><Link href={`/review/${id}`}>Review case and protected original</Link></nav>
    <p>Read-only history. Times and identities are shown only when recorded. A historical event is not automatically a superseded decision.</p>
    <FilterForm onSubmit={apply} legend="Audit filters" disabled={loading}>
      <label>Event kind<select name="kind" {...validation('kind')}><option value="">All kinds</option>
        {Object.entries(AUDIT_LABELS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>
      <label>Recorded actor ID<input name="actor_id" {...validation('actor_id')} /></label>
      <label>From local time (inclusive)<input type="datetime-local" step="1" name="time_from" {...validation('time_from')} /></label>
      <label>To local time (inclusive)<input type="datetime-local" step="1" name="time_to" {...validation('time_to')} /></label>
      <label>Events per page<select name="limit" defaultValue="50" {...validation('limit')}><option>20</option><option>50</option><option>100</option></select></label>
      <button type="submit" disabled={loading}>Apply audit filters</button>
      <button type="reset" disabled={loading} onClick={() => { setFieldError(null); setFilters({ offset: 0, limit: 50 }); }}>Clear filters</button>
      {fieldError && <p id="audit-filter-error" className="audit-field-error" role="alert">{fieldError.message}</p>}
    </FilterForm>
    <button onClick={() => setGeneration(value => value + 1)} disabled={loading}>Refresh audit</button>
    <div role="status" aria-live="polite">{loading ? 'Loading audit…' : data ? `${data.total} matching events · Case revision ${data.revision}` : ''}</div>
    {error && <p role="alert">{error}</p>}
    {data && <><p>Showing {data.events.length ? data.offset + 1 : 0}–{data.offset + data.events.length} of {data.total}. Filter bounds include both endpoints; events without recorded timestamps cannot match a time range.</p>
      {!data.events.length ? <p>No recorded events match these filters.</p> : <Timeline label="Invoice audit chronology" className="audit-timeline">{data.events.map(event => <EventRecord key={event.id} event={event} />)}</Timeline>}
      <nav aria-label="Audit event pages"><button disabled={loading || data.offset === 0} onClick={() => setFilters(current => ({ ...current, offset: Math.max(0, data.offset - data.limit) }))}>Previous events</button>
        <button disabled={loading || data.offset + data.limit >= data.total} onClick={() => setFilters(current => ({ ...current, offset: data.offset + data.limit }))}>Next events</button></nav>
    </>}
  </main>;
}
export default function AuditPage() {
  const { id } = useParams<{ id: string }>();
  return <AuditCase key={id} id={id} />;
}
