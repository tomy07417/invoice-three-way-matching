'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from '../../../lib/api';
import type { ParameterHistory, ParameterVersion, RuleValues } from '../../../lib/contracts';
import { isCancelled } from '../../../lib/intake-state';
import { reviewError } from '../../../lib/review-state';
import { fractionToPercent, parameterDraft, requiresParameterRefresh, validateParameters, type ParameterDraft } from '../../../lib/manager-state';
import { useSession } from '../../../components/intake/session-gate';
import { MoneyValue, TimestampValue } from '../../../components/shared/format-value';
import { CommentField, TableRegion } from '../../../components/shared/workspace';
import { confirmAction } from '../../../components/shared/confirmation';

const LIMIT = 10;
function Values({ values }: { values: RuleValues | null }) {
  return values ? <>{fractionToPercent(values.price_tolerance)}% tolerance; <MoneyValue amount={values.analyst_approval_limit} currency="USD" /> Analyst limit</> : <>Unknown — no recorded previous values</>;
}
export default function ParametersPage() {
  const { session, handleError } = useSession();
  const [current, setCurrent] = useState<ParameterVersion | null>(null);
  const [history, setHistory] = useState<ParameterHistory | null>(null);
  const [draft, setDraft] = useState<ParameterDraft>({ tolerancePercent: '', analystLimit: '', comment: '' });
  const [errors, setErrors] = useState<Partial<Record<keyof ParameterDraft, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [paused, setPaused] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const firstInvalid = useRef<HTMLInputElement>(null);
  const secondInvalid = useRef<HTMLInputElement>(null);
  const commentInvalid = useRef<HTMLTextAreaElement>(null);
  const dirty = !!current && JSON.stringify(draft) !== JSON.stringify(parameterDraft(current));
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError(null);
    try {
      const next = await api.parameters(signal);
      if (signal?.aborted || !mounted.current) return;
      setCurrent(next); setDraft(parameterDraft(next)); setErrors({}); setPaused(false); setNotice(null);
    } catch (failure) {
      if (isCancelled(failure) || !mounted.current) return;
      handleError(failure); setError(reviewError(failure)); setPaused(true);
    } finally { if (!signal?.aborted && mounted.current) setLoading(false); }
  }, [handleError]);
  const loadHistory = useCallback(async (offset: number, signal?: AbortSignal) => {
    setHistoryLoading(true); setHistoryError(null);
    try {
      const next = await api.parameterHistory({ offset, limit: LIMIT }, signal);
      if (!signal?.aborted && mounted.current) setHistory(next);
    } catch (failure) {
      if (isCancelled(failure) || !mounted.current) return;
      handleError(failure); setHistoryError(reviewError(failure));
    } finally { if (!signal?.aborted && mounted.current) setHistoryLoading(false); }
  }, [handleError]);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController(); void load(controller.signal); void loadHistory(0, controller.signal);
    return () => { mounted.current = false; controller.abort(); };
  }, [load, loadHistory]);
  function refresh() {
    if (inFlight.current || loading) return;
    if (dirty && !confirmAction('Refresh replaces your unsaved parameter draft and optional comment with current server values. Continue?')) return;
    void load();
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!current || loading || paused || inFlight.current) return;
    const result = validateParameters(draft, current); setErrors(result.errors); setNotice(null); setError(null);
    if (!result.values) {
      (result.errors.tolerancePercent ? firstInvalid : result.errors.analystLimit ? secondInvalid : commentInvalid).current?.focus();
      return;
    }
    if (!confirmAction(`Save rule parameters?\nTolerance: ${draft.tolerancePercent}%\nAnalyst limit: ${draft.analystLimit} USD\nOnly future evaluations and reevaluations use these values. Historical outcomes and existing approval-limit snapshots remain unchanged.\nUnchanged values still check the server version but create no audit event; their optional comment is not recorded.`)) return;
    inFlight.current = true; setBusy(true);
    try {
      // A no-op still goes to the server: stale versions must conflict even without value changes.
      const values = result.changed ? result.values : { price_tolerance: current.price_tolerance };
      const next = await api.updateParameters(current.version_id, values, session.csrf_token);
      if (!mounted.current) return;
      setCurrent(next); setDraft(parameterDraft(next));
      setNotice(next.version_id === current.version_id ? 'No change: the current version was retained and no audit event or comment was recorded.' : `Saved version ${next.version_id}. Future evaluations and reevaluations will use the new values.`);
      void loadHistory(0);
    } catch (failure) {
      if (!mounted.current) return;
      handleError(failure); setError(reviewError(failure));
      if (requiresParameterRefresh(failure)) setPaused(true);
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  const blocked = busy || loading || paused;
  return <main><h1>Rule parameters</h1>
    <p className="manager-warning">Changes apply only to future evaluations and explicit reevaluations. Historical automatic outcomes and existing evaluated approval-limit snapshots remain unchanged. Limits are in USD; no foreign-exchange conversion is performed.</p>
    <button onClick={refresh} disabled={busy || loading}>Refresh current parameters</button>
    {loading && <p role="status">Loading current parameters…</p>}
    {error && <p role="alert" className="manager-error">{error}</p>}
    {paused && <p role="status" className="manager-warning">Saving is paused after a failed refresh, version conflict or uncertain save. Your draft is retained. Explicitly refresh and review current values before retrying; do not blindly repeat the request.</p>}
    {notice && <p role="status">{notice}</p>}
    {current && <><section aria-labelledby="current-parameters"><h2 id="current-parameters">Current recorded values</h2>
      <p><Values values={current} /></p><p>Version {current.version_id} · Effective <TimestampValue value={current.effective_from} /></p>
      <p>Actor: {current.actor ? `${current.actor.username} (${current.actor.role})` : `Not recorded as a user — ${current.actor_reference}`}</p>
    </section><section><h2>Edit future rules</h2><p>Enter finite decimals ≥ 0. No fixed maximum or decimal scale. Scientific notation is accepted; tolerance is a percentage, not a fraction (2 means 2%). A comment is optional.</p>
      <form onSubmit={save} noValidate>
        <label htmlFor="manager-tolerance">Price tolerance per line (%)<input ref={firstInvalid} id="manager-tolerance" type="text" value={draft.tolerancePercent} disabled={blocked}
          aria-invalid={!!errors.tolerancePercent} aria-describedby={errors.tolerancePercent ? 'tolerance-error' : undefined}
          onChange={event => { setDraft({ ...draft, tolerancePercent: event.target.value }); setErrors({ ...errors, tolerancePercent: undefined }); }} /></label>
        {errors.tolerancePercent && <p id="tolerance-error" className="manager-error">{errors.tolerancePercent}</p>}
        <label htmlFor="manager-limit">Analyst approval limit (USD)<input ref={secondInvalid} id="manager-limit" type="text" value={draft.analystLimit} disabled={blocked}
          aria-invalid={!!errors.analystLimit} aria-describedby={errors.analystLimit ? 'limit-error' : undefined}
          onChange={event => { setDraft({ ...draft, analystLimit: event.target.value }); setErrors({ ...errors, analystLimit: undefined }); }} /></label>
        {errors.analystLimit && <p id="limit-error" className="manager-error">{errors.analystLimit}</p>}
        <CommentField id="manager-comment" inputRef={commentInvalid} required={false} value={draft.comment} disabled={blocked}
          error={errors.comment} onChange={value => { setDraft({ ...draft, comment: value }); setErrors({ ...errors, comment: undefined }); }} />
        <button type="submit" disabled={blocked}>{busy ? 'Saving…' : 'Review and save parameters'}</button>
      </form></section></>}
    <section aria-labelledby="parameter-history"><h2 id="parameter-history">Parameter history</h2>
      <p>Recorded before/after values are historical evidence. Bootstrap and missing historical identity are not Manager actions. History is refreshed manually, independently of your draft.</p>
      <button disabled={historyLoading} onClick={() => void loadHistory(history?.offset ?? 0)}>Refresh history</button>
      {historyLoading && <p role="status">Loading parameter history…</p>}{historyError && <p role="alert">History could not be refreshed: {historyError} Previously loaded entries may be outdated.</p>}
      {history && <><p>{history.total} recorded versions · Offset {history.offset}</p>
        {!history.items.length && <p>No history entries on this page.</p>}
        <TableRegion label="Versioned rule history"><table><caption>Versioned rule history (recorded instants, localized display)</caption><thead><tr><th>Version / time</th><th>Before</th><th>After</th><th>Actor / provenance</th><th>Comment</th></tr></thead><tbody>
          {history.items.map(item => <tr key={item.version_id}><td>Version {item.version_id} · Previous {item.previous_version_id ?? 'Unknown / none'}<br /><TimestampValue value={item.effective_from} /></td>
            <td><Values values={item.before} /></td><td><Values values={item.after} /></td>
            <td>{item.actor ? `${item.actor.username} (${item.actor.role}, ID ${item.actor.actor_id})` : `No recorded user — ${item.actor_reference}`}<br />{item.provenance.replaceAll('_', ' ')}</td><td>{item.comment ?? 'No comment recorded'}</td></tr>)}
        </tbody></table></TableRegion><div className="manager-actions"><button disabled={historyLoading || history.offset === 0} onClick={() => void loadHistory(Math.max(0, history.offset - LIMIT))}>Previous history page</button>
          <button disabled={historyLoading || history.offset + history.limit >= history.total} onClick={() => void loadHistory(history.offset + history.limit)}>Next history page</button></div></>}
    </section>
  </main>;
}
