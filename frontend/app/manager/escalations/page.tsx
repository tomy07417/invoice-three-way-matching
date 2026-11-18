'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../../lib/api';
import type { ReviewDetail, ReviewHistory, ReviewQueuePage } from '../../../lib/contracts';
import { useSession } from '../../../components/intake/session-gate';
import { isCancelled } from '../../../lib/intake-state';
import { reviewError } from '../../../lib/review-state';
import { escalationComments } from '../../../lib/manager-state';
import { MoneyValue, TimestampValue } from '../../../components/shared/format-value';
import { TableRegion } from '../../../components/shared/workspace';

const LIMIT = 20;
function SelectedEscalation({ id }: { id: string }) {
  const { handleError } = useSession();
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [history, setHistory] = useState<ReviewHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null); setDetail(null); setHistory(null);
    async function load() {
      try {
        const next = await api.reviewDetail(id, controller.signal);
        const events = await api.reviewHistory(id, controller.signal);
        if (controller.signal.aborted) return;
        if (events.revision !== next.revision) throw new ApiError(409, 'stale_revision', 'The case changed while loading selected evidence.', false);
        setDetail(next); setHistory(events);
      } catch (failure) {
        if (isCancelled(failure) || controller.signal.aborted) return;
        handleError(failure); setError(reviewError(failure));
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load(); return () => controller.abort();
  }, [id, generation, handleError]);
  const comments = escalationComments(history?.events ?? []);
  return <section aria-labelledby="selected-escalation"><h2 id="selected-escalation">Selected case: Analyst comments and evidence</h2>
    <p>Case {id}. Only this selected case is loaded; queue rows are not individually polled.</p>
    <button disabled={loading} onClick={() => setGeneration(value => value + 1)}>Refresh selected evidence</button>
    {loading && <p role="status">Loading selected case and recorded history…</p>}
    {error && <p role="alert">{error} Selected evidence is unavailable; no decision is implied.</p>}
    {detail && history && <><p>Revision {detail.revision} · Operational state: {detail.operational_state.replaceAll('_', ' ')}</p>
      {detail.operational_state !== 'ESCALATED' && <p className="manager-warning" role="status">This case is no longer escalated. Refresh the queue; the decision workflow will recheck current eligibility.</p>}
      <dl><dt>Amount / currency (current extraction)</dt><dd><MoneyValue amount={detail.extraction?.total_amount} currency={detail.extraction?.currency} /></dd>
        <dt>Automatic outcome</dt><dd>{detail.evaluation?.result.final_status ?? 'Incomplete — no final outcome'}</dd>
        <dt>Server-recorded eligibility reason</dt><dd>{detail.eligibility.reason ?? 'No reason recorded'}</dd>
        <dt>Evaluated Analyst-limit snapshot</dt><dd><MoneyValue amount={detail.eligibility.limit_amount} currency={detail.eligibility.limit_currency} />. No FX conversion.</dd>
        <dt>Payment state</dt><dd>{detail.payment_blocked ? 'Payment blocked' : 'Payment authorized — not executed'}</dd></dl>
      <h3>Recorded escalation comments</h3>
      <p>Roles below are recorded provenance, not inferred from amount. A missing role is not assumed to be an Analyst.</p>
      {!comments.length && <p>No escalation comment is available in the recorded history.</p>}
      <ol>{comments.map((event, index) => <li key={index}><p>{event.actor_role === 'ap_analyst' ? 'Analyst comment' : 'Escalation comment'}: {event.comment || 'No comment recorded'}</p>
        <p>{event.actor_name ?? `Actor reference ${event.actor_id}`} · {event.actor_role ?? 'Role not recorded'} · <TimestampValue value={event.at} /> · {event.current ? 'Current' : 'Historical'}</p></li>)}</ol>
      <h3>Automatic evaluation reasons</h3><ul>{detail.evaluation?.result.rules.filter(rule => rule.outcome !== 'PASS').map((rule, index) => <li key={index}>{rule.reason}</li>)}</ul>
      {!detail.evaluation && <p>No evaluation evidence available.</p>}
      <p>{detail.requires_reevaluation ? 'Reevaluation is required before approval.' : 'Review the full PO / GRN evidence before deciding.'}</p>
      <Link href={`/review/${id}`}>Open AP resolution: approve / reject with required comment and confirmation</Link>
      <p>Decisions use the existing versioned AP workflow. The server rechecks authorization, eligibility, credit-note blocks and available quantities; this page never infers eligibility from amount.</p>
    </>}
  </section>;
}
export default function EscalationsPage() {
  const { handleError } = useSession();
  const [page, setPage] = useState<ReviewQueuePage | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const load = useCallback(async (offset: number, signal?: AbortSignal) => {
    if (inFlight.current && !signal) return;
    inFlight.current = true; setLoading(true); setError(null);
    try {
      // Explicit 'all' is essential: escalations may be Rejected or Auto-Pay, not just Held.
      const next = await api.reviewList({ operational_state: 'ESCALATED', outcome: 'all', offset, limit: LIMIT }, signal);
      if (!signal?.aborted && mounted.current) { setPage(next); setSelected(null); }
    } catch (failure) {
      if (isCancelled(failure) || !mounted.current) return;
      handleError(failure); setError(reviewError(failure));
    } finally {
      inFlight.current = false;
      if (!signal?.aborted && mounted.current) setLoading(false);
    }
  }, [handleError]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController(); void load(0, controller.signal);
    return () => { mounted.current = false; controller.abort(); };
  }, [load]);
  return <main><h1>Pending Manager escalations</h1><p>All automatic outcomes are included. Amounts and currencies are shown exactly as recorded. No client-side matching or foreign-exchange conversion.</p>
    <button disabled={loading} onClick={() => void load(page?.offset ?? 0)}>Refresh escalation queue</button>
    {loading && <p role="status">Loading escalations…</p>}
    {error && <p role="alert">{error} Previously loaded queue rows may be outdated. Refresh before acting.</p>}
    {page && <><p>{page.total} pending escalations · Offset {page.offset}</p>
      {!page.items.length && <p>No pending escalations on this page.</p>}
      <TableRegion label="Pending Manager cases"><table><caption>Pending cases — select one to load the Analyst comment and current evidence</caption><thead><tr><th>Invoice / supplier</th><th>Amount / currency</th><th>Automatic outcome</th><th>Escalation / evaluation reasons</th><th>Evidence</th></tr></thead>
        <tbody>{page.items.map(item => <tr key={item.invoice_id}><td>{item.invoice_number ?? item.invoice_id}<br />{item.supplier_name ?? 'Supplier unknown'}</td>
          <td><MoneyValue amount={item.total_amount} currency={item.currency} /></td><td>{item.automatic_outcome ?? 'Incomplete — no final outcome'}</td>
          <td>{item.reasons.length ? <ul>{item.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : 'No reason in queue snapshot; select for server eligibility evidence.'}</td>
          <td><button disabled={loading} aria-pressed={selected === item.invoice_id} aria-controls="escalation-evidence" onClick={() => setSelected(item.invoice_id)}>View comments: {item.invoice_number ?? item.invoice_id}</button></td></tr>)}</tbody></table></TableRegion>
      <div className="manager-actions"><button disabled={loading || page.offset === 0} onClick={() => void load(Math.max(0, page.offset - LIMIT))}>Previous escalation page</button>
        <button disabled={loading || page.offset + page.limit >= page.total} onClick={() => void load(page.offset + page.limit)}>Next escalation page</button></div></>}
    <div id="escalation-evidence" aria-live="polite">{selected ? <SelectedEscalation key={selected} id={selected} /> : <p>Select a case to view its recorded Analyst comment and evidence.</p>}</div>
  </main>;
}
