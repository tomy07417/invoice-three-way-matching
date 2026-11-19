'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../../lib/api';
import type { InvoiceDraft, ReviewAction, ReviewDetail, ReviewHistory } from '../../../lib/contracts';
import { displayValue, isCancelled } from '../../../lib/intake-state';
import { HEADER_LABELS, currentDecisionLabel, decisionConfirmation, requiredReviewComment, reviewError } from '../../../lib/review-state';
import { useSession } from '../../../components/intake/session-gate';
import { DraftEditor } from '../../../components/review/draft-editor';
import { MoneyValue, TimestampValue } from '../../../components/shared/format-value';
import { CommentField, EvidencePanel, OriginalDocument, StatusBadge, TableRegion, Timeline, TimelineEvent } from '../../../components/shared/workspace';
import { confirmAction } from '../../../components/shared/confirmation';

export default function ReviewCasePage() {
  const { id } = useParams<{ id: string }>();
  // A route change gets new state/refs; old in-flight responses cannot label another case.
  return <ReviewCase key={id} id={id} />;
}

function ReviewCase({ id }: { id: string }) {
  const { session, handleError } = useSession();
  const [data, setData] = useState<ReviewDetail | null>(null);
  const [history, setHistory] = useState<ReviewHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [comment, setComment] = useState('');
  const [commentError, setCommentError] = useState<string | null>(null);
  const commentRef = useRef<HTMLTextAreaElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(null);
    try {
      const next = await api.reviewDetail(id, signal);
      const events = await api.reviewHistory(id, signal);
      if (next.revision !== events.revision) throw new ApiError(409, 'stale_revision', 'The case changed while loading.', false);
      if (signal?.aborted || !mounted.current) return;
      setData(next); setHistory(events); setStale(false); setEditing(false); setDirty(false);
    } catch (failure) {
      if (isCancelled(failure) || !mounted.current) return;
      handleError(failure); setError(reviewError(failure)); setStale(true);
    } finally { if (!signal?.aborted && mounted.current) setLoading(false); }
  }, [id, handleError]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController(); void load(controller.signal);
    return () => { mounted.current = false; controller.abort(); };
  }, [load]);
  function refresh() {
    if (dirty && !confirmAction('Refresh discards unsaved correction changes. Continue?')) return;
    void load();
  }
  async function mutate(operation: () => Promise<ReviewDetail>, success: string, confirmation?: string) {
    if (inFlight.current || stale || !data) return;
    if (confirmation && !confirmAction(confirmation)) return;
    inFlight.current = true; setBusy(true); setError(null); setNotice(null);
    try {
      const next = await operation();
      if (!mounted.current) return;
      setData(next); setEditing(false); setDirty(false); setComment(''); setCommentError(null); setNotice(success);
      const events = await api.reviewHistory(id);
      if (events.revision !== next.revision) throw new ApiError(409, 'stale_revision', 'The case changed after this action.', false);
      if (mounted.current) { setHistory(events); setStale(false); }
    } catch (failure) {
      if (!mounted.current) return;
      handleError(failure); setError(reviewError(failure));
      // Never replay an uncertain mutation. Refetch current state and history first.
      if (!(failure instanceof ApiError) || [0, 409, 428, 502, 503, 504].includes(failure.status)) setStale(true);
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  function act(action: ReviewAction | 'reevaluate' | 'escalate') {
    if (!data) return;
    try {
      const reason = requiredReviewComment(comment);
      setCommentError(null);
      if (action === 'reevaluate') void mutate(() => api.reevaluate(id, data.revision, reason, session.csrf_token),
        'Reevaluation recorded. Complete Auto-Pay is approved automatically unless a prior rejection or credit-note request blocks it.',
        'Reevaluation preserves a human rejection until correction and revokes any existing approval. Complete Auto-Pay is approved automatically unless blocked. Continue?');
      else if (action === 'escalate') void mutate(() => api.escalate(id, data.revision, reason, session.csrf_token), 'Manager review requested and audited.');
      else void mutate(() => api.decide(id, data.revision, action, reason, session.csrf_token),
        'Human decision recorded with comment and audit.', decisionConfirmation(action));
    } catch (failure) { setCommentError(reviewError(failure)); commentRef.current?.focus(); }
  }
  function saveCorrection(invoice: InvoiceDraft, reason: string) {
    if (!data) return;
    void mutate(() => api.correctExtraction(id, data.revision, invoice, reason, session.csrf_token),
      'Correction saved. Reevaluate before making a new decision.',
      'Save correction, revoke any previous approval and release its quantities with audit?');
  }
  const blocked = busy || stale || loading;
  const decided = data?.current_decision !== null;
  const canDecide = !!data && !blocked && !data.requires_reevaluation && !decided;
  const canApprove = canDecide && data.eligibility.allowed && !data.credit_note_pending
    && data.evaluation?.result.final_status != null && data.comparison_rows.length > 0
    && data.comparison_rows.every(row => row.association_status === 'explicit');
  return <main className="review-workspace"><Link href="/review">Back to AP queue</Link><h1>AP invoice review</h1>
    <button onClick={refresh} disabled={busy || loading}>Refresh case</button>
    {loading && <p role="status">Loading current case and history…</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {stale && <p className="notice">Actions are paused. Refresh to review the latest version; do not blindly repeat a decision.</p>}
    {data && <><section className="panel"><h2>{data.extraction?.invoice_number ?? data.invoice_id}</h2>
      <p>Case revision <strong>{data.revision}</strong> · Extraction version {data.extraction_version}</p>
      <dl><dt>Automatic outcome</dt><dd>{data.evaluation?.result.final_status ?? 'Incomplete — no final outcome'}</dd>
        <dt>Operational state</dt><dd><StatusBadge label={data.operational_state.replaceAll('_', ' ')} tone={data.payment_blocked ? 'warning' : 'positive'} /></dd>
        <dt>Payment</dt><dd>{data.payment_blocked ? 'Payment blocked' : 'Payment authorized — not executed'}</dd>
        <dt>Analyst limit</dt><dd><MoneyValue amount={data.eligibility.limit_amount} currency={data.eligibility.limit_currency} /> inclusive; other currencies require Manager, without FX.</dd></dl>
      {data.eligibility.reason && <p>{data.eligibility.reason}</p>}
      {data.requires_reevaluation && <p role="status">Reevaluation required. Earlier evidence cannot authorize a decision.</p>}
      {data.credit_note_pending && <p role="status">Credit Note Requested: payment remains blocked while the request is pending.</p>}
      {data.current_decision && <p>{currentDecisionLabel(data.current_decision.actor_id)}: {data.current_decision.action.replaceAll('_', ' ')} — {data.current_decision.comment}</p>}
    </section>
    <div className="review-document-grid"><section className="panel"><h2>Private original</h2>
      <OriginalDocument invoiceId={id} title="Original invoice document" openLabel="Open original" preview download />
    </section><section className="panel"><h2>Original extraction versus current version</h2>
      <TableRegion label="Original and current extraction"><table><caption>Original and current extracted headers</caption><thead><tr><th>Field</th><th>Original</th><th>Current</th></tr></thead><tbody>
        {Object.entries(HEADER_LABELS).map(([key, label]) => <tr key={key}><th>{label}</th>
          <td>{['subtotal', 'tax_amount', 'total_amount'].includes(key)
            ? <MoneyValue amount={data.original_extraction?.[key as keyof typeof HEADER_LABELS]} currency={data.original_extraction?.currency} />
            : displayValue(data.original_extraction?.[key as keyof typeof HEADER_LABELS])}</td>
          <td>{['subtotal', 'tax_amount', 'total_amount'].includes(key)
            ? <MoneyValue amount={data.extraction?.[key as keyof typeof HEADER_LABELS]} currency={data.extraction?.currency} />
            : displayValue(data.extraction?.[key as keyof typeof HEADER_LABELS])}</td></tr>)}
        <tr><th>PO references</th><td>{data.original_extraction?.po_references?.join(', ') ?? 'Unknown'}</td>
          <td>{data.extraction?.po_references?.join(', ') ?? 'Unknown'}</td></tr>
      </tbody></table></TableRegion></section></div>
    <section className="panel"><h2>Invoice / PO / GRN comparison</h2>
      <p>Evaluation-time evidence, not a live reservation. Shared-target rows show the same available balance; approval rechecks grouped quantities transactionally. Unknown evidence is not zero.</p>
      <TableRegion label="Invoice, PO and GRN comparison"><table><caption>Exact quantities, net prices and explicit PO-local links</caption><thead><tr>
        <th>Invoice line</th><th>PO / line</th><th>Ordered</th><th>Invoiced</th><th>Received</th><th>Previously approved</th><th>Available</th>
        <th>PO net price</th><th>Invoice net price</th><th>Tolerance fraction</th><th>Discrepancies</th>
      </tr></thead><tbody>{data.comparison_rows.map(row => <tr key={row.line_index}>
        <td>{row.line_index + 1}. {displayValue(row.description)}</td><td>{row.po_number ?? 'Unresolved'} / {displayValue(row.po_line_number)}</td>
        <td>{displayValue(row.quantity_ordered)}</td><td>{displayValue(row.quantity_invoiced)}</td><td>{displayValue(row.quantity_received)}</td>
        <td>{displayValue(row.quantity_previously_approved)}</td><td>{displayValue(row.quantity_available)}</td>
        <td><MoneyValue amount={row.po_net_unit_price} currency={row.po_currency} /></td><td><MoneyValue amount={row.invoice_net_unit_price} currency={row.invoice_currency} /></td>
        <td>{displayValue(row.tolerance)} (0.02 = 2%)</td><td><ul>{row.findings.filter(finding => finding.evidence.outcome !== 'PASS').map((finding, index) => <li key={index}>{finding.message}</li>)}</ul></td>
      </tr>)}</tbody></table></TableRegion>
      {!data.comparison_rows.length && <p>No complete comparison evidence. Correct missing facts and reevaluate; no approval is implied.</p>}
      <EvidencePanel title="Rules requiring attention"><ul>{data.evaluation?.result.rules.filter(rule => rule.outcome !== 'PASS').map((rule, index) => <li key={index}>{rule.reason}</li>)}</ul></EvidencePanel>
    </section>
    {!editing ? <button disabled={blocked} onClick={() => setEditing(true)}>Correct extraction</button>
      : <DraftEditor key={data.revision} initial={data.extraction} busy={blocked} onSave={saveCorrection}
          onDirty={() => setDirty(true)} onCancel={() => { setEditing(false); setDirty(false); }} />}
    <section className="panel"><h2>Human decision and reevaluation</h2>
      <CommentField id="review-comment" inputRef={commentRef} label="Comment / reason" value={comment}
        onChange={value => { setComment(value); setCommentError(null); }} disabled={blocked} error={commentError ?? undefined}
        help="Explain the decision or reevaluation. Comments remain in the audit trail." />
      <p>Approval requires complete evidence, reliable associations and currently available receipts. Overrides cannot bypass these safeguards.</p>
      <div className="review-actions"><button onClick={() => act('approve')} disabled={!canApprove || editing}>Approve</button>
        <button onClick={() => act('reject')} disabled={!canDecide || editing}>Reject invoice</button>
        <button onClick={() => act('request_credit_note')} disabled={!canDecide || editing}>Request credit note</button>
        <button onClick={() => act('escalate')} disabled={!canDecide || !data.eligibility.requires_manager || editing}>Request Manager review</button>
        <button onClick={() => act('reevaluate')} disabled={blocked || editing}>Reevaluate</button></div>
    </section>
    {history && history.revision === data.revision && <section className="panel"><h2>Case history and audit</h2><Timeline label="Case history and audit" className="review-history">
      {history.events.map((event, index) => <TimelineEvent key={index}
        title={`${event.kind.replaceAll('_', ' ')} · ${event.current ? 'Current' : 'Historical / audit'}`}
        metadata={<><TimestampValue value={event.at} /> · {event.actor_name ?? `Actor ${event.actor_id}`} {event.actor_role ?? ''}</>}>
        <p>{event.comment}</p>{event.action && <p>Decision: {event.action.replaceAll('_', ' ')}</p>}
        {event.evaluation && <p>Automatic outcome: {event.evaluation.result.final_status ?? 'Incomplete'}</p>}
        <details><summary>Version and evidence details</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
      </TimelineEvent>)}</Timeline></section>}
    </>}
  </main>;
}
