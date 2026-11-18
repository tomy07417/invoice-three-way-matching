'use client';

import Link from 'next/link';
import { useCallback, useState, type FormEvent } from 'react';
import { api } from '../../lib/api';
import type { ReviewListOptions, ReviewQueuePage } from '../../lib/contracts';
import { displayValue } from '../../lib/intake-state';
import { REVIEW_STATES } from '../../lib/review-state';
import { usePolling } from '../../components/intake/use-polling';
import { ReadFeedback } from '../../components/intake/status';

const manualRefresh = (_page: ReviewQueuePage) => false;
export default function ReviewQueue() {
  const [options, setOptions] = useState<ReviewListOptions>({ offset: 0, limit: 20 });
  const load = useCallback((signal: AbortSignal) => api.reviewList(options, signal), [options]);
  const read = usePolling(load, manualRefresh);
  const page = read.data;
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const text = (key: string) => String(form.get(key) ?? '').trim() || undefined;
    setOptions({ offset: 0, limit: 20, supplier: text('supplier'), date_from: text('date_from'), date_to: text('date_to'),
      min_amount: text('min_amount'), max_amount: text('max_amount'), reason: text('reason'),
      operational_state: text('operational_state') as ReviewListOptions['operational_state'],
      min_age_days: text('min_age_days') ? Number(text('min_age_days')) : undefined,
      sort: text('sort') as ReviewListOptions['sort'], direction: text('direction') as ReviewListOptions['direction'],
      outcome: text('outcome') as ReviewListOptions['outcome'] });
  }
  return <main className="review-workspace"><h1>AP review queue</h1>
    <p>Review held cases, compare evidence and track human decisions. Automatic outcomes do not execute payments.</p>
    <form className="review-filters" onSubmit={filter}>
      <label htmlFor="review-supplier">Supplier<input id="review-supplier" name="supplier" maxLength={200} /></label>
      <label>Invoice date from<input name="date_from" type="date" /></label>
      <label>Invoice date to<input name="date_to" type="date" /></label>
      <label>Minimum amount<input name="min_amount" inputMode="decimal" /></label>
      <label>Maximum amount<input name="max_amount" inputMode="decimal" /></label>
      <label>Discrepancy reason<input name="reason" maxLength={200} /></label>
      <label>Operational state<select name="operational_state"><option value="">All states</option>
        {REVIEW_STATES.map(state => <option key={state} value={state}>{state.replaceAll('_', ' ')}</option>)}
      </select></label>
      <label>Minimum age (days)<input name="min_age_days" type="number" min={0} max={36500} step={1} /></label>
      <label>Automatic outcome<select name="outcome" defaultValue="Held for Review">
        <option>Held for Review</option><option>Rejected</option><option>Auto-Pay</option><option value="all">All outcomes / incomplete cases</option>
      </select></label>
      <label>Order by<select name="sort"><option value="created_at">Receipt time</option><option value="age">Age</option>
        <option value="amount">Amount</option><option value="supplier">Supplier</option><option value="invoice_date">Invoice date</option>
      </select></label>
      <label>Direction<select name="direction"><option value="asc">Ascending</option><option value="desc">Descending</option></select></label>
      <button type="submit">Apply review filters</button>
    </form>
    <button onClick={read.refresh} disabled={read.loading}>Refresh queue</button>
    <ReadFeedback loading={read.loading} error={read.error} paused={read.paused} refresh={read.refresh} />
    {page && <><p>{page.total} matching cases</p><div className="table-scroll"><table><caption>AP cases and payment blocks</caption>
      <thead><tr><th>Invoice</th><th>Supplier / date</th><th>Amount</th><th>Automatic outcome</th><th>Operational state</th><th>Reasons</th><th>Age / receipt time</th></tr></thead>
      <tbody>{page.items.map(item => <tr key={item.invoice_id}>
        <td><Link href={`/review/${item.invoice_id}`}>{item.invoice_number ?? item.invoice_id}</Link></td>
        <td>{displayValue(item.supplier_name)}<br />{displayValue(item.invoice_date)}</td>
        <td>{displayValue(item.total_amount)} {displayValue(item.currency)}</td>
        <td>{item.automatic_outcome ?? 'Incomplete — no final outcome'}</td>
        <td>{item.operational_state.replaceAll('_', ' ')}<br />{item.payment_blocked ? 'Payment blocked' : 'Payment authorized, not executed'}
          {item.requires_manager && <p>Manager required</p>}</td>
        <td><ul>{item.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></td>
        <td>{Math.floor(item.age_seconds / 86400)} days<br /><time>{item.created_at}</time></td>
      </tr>)}</tbody></table></div>
      {!page.items.length && <p role="status">No matching review cases. Change filters or refresh the queue.</p>}
      <nav aria-label="Review queue pages"><button disabled={read.loading || page.offset === 0}
        onClick={() => setOptions(value => ({ ...value, offset: Math.max(0, page.offset - page.limit) }))}>Previous review page</button>
        <button disabled={read.loading || page.offset + page.limit >= page.total}
          onClick={() => setOptions(value => ({ ...value, offset: page.offset + page.limit }))}>Next review page</button></nav>
    </>}
  </main>;
}
