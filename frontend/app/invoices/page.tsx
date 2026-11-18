'use client';

import Link from 'next/link';
import { useCallback, useState } from 'react';
import { api } from '../../lib/api';
import type { InvoicePage, ProcessingState } from '../../lib/contracts';
import { displayValue, isActive, outcomeLabel, PROCESSING_STATES } from '../../lib/intake-state';
import { usePolling } from '../../components/intake/use-polling';
import { ReadFeedback } from '../../components/intake/status';
import { MoneyValue, TimestampValue } from '../../components/shared/format-value';
import { TableRegion } from '../../components/shared/workspace';

const hasActiveJobs = (page: InvoicePage) => page.items.some(invoice => isActive(invoice.processing_status));
export default function InvoicesPage() {
  const [status, setStatus] = useState<ProcessingState | ''>('');
  const [offset, setOffset] = useState(0);
  const load = useCallback((signal: AbortSignal) => api.list({ offset, limit: 20, status: status || undefined }, signal), [offset, status]);
  const read = usePolling(load, hasActiveJobs);
  const page = read.data;
  return <main><div className="heading"><h1>Invoices</h1><Link className="button" href="/invoices/new">New invoice</Link></div>
    <label htmlFor="processing-filter">Technical state</label>
    <select id="processing-filter" value={status} onChange={event => { setStatus(event.target.value as ProcessingState | ''); setOffset(0); }}>
      <option value="">All states</option>{PROCESSING_STATES.map(state => <option key={state}>{state}</option>)}
    </select>
    <ReadFeedback {...read} />
    {page && <>
      <p>{page.total} invoice{page.total === 1 ? '' : 's'}</p>
      {page.items.length === 0 ? <p>No invoices on this page. Choose another filter or upload a new invoice.</p> :
        <TableRegion label="Invoice list">
          <table><caption>Invoices — unknown draft values appear as —</caption><thead><tr>
            <th scope="col">Invoice number / ID</th><th scope="col">Supplier</th><th scope="col">Invoice date</th>
            <th scope="col">Total</th><th scope="col">Currency</th><th scope="col">Technical state</th>
            <th scope="col">Automatic outcome</th><th scope="col">Received</th>
          </tr></thead><tbody>{page.items.map(invoice => <tr key={invoice.invoice_id}>
            <td><Link href={`/invoices/${invoice.invoice_id}`}>{displayValue(invoice.invoice_number)}<small>{invoice.invoice_id}</small></Link></td>
            <td>{displayValue(invoice.supplier_name)}</td><td>{displayValue(invoice.invoice_date)}</td>
            <td><MoneyValue amount={invoice.total_amount} currency={invoice.currency} /></td><td>{displayValue(invoice.currency)}</td>
            <td>{invoice.processing_status}</td><td>{outcomeLabel(invoice.processing_status, invoice.automatic_outcome)}</td><td><TimestampValue value={invoice.created_at} /></td>
          </tr>)}</tbody></table>
        </TableRegion>}
      <nav aria-label="Invoice pagination" className="actions">
        <button disabled={offset === 0 || read.loading} onClick={() => setOffset(value => Math.max(0, value - 20))}>Previous</button>
        <span>Page {Math.floor(offset / 20) + 1}</span>
        <button disabled={offset + 20 >= page.total || read.loading} onClick={() => setOffset(value => value + 20)}>Next</button>
      </nav>
    </>}
  </main>;
}
