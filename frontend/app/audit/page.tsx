'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useState, type FormEvent } from 'react';
import { api } from '../../lib/api';
import type { ReviewQueuePage } from '../../lib/contracts';
import { displayValue } from '../../lib/intake-state';
import { usePolling } from '../../components/intake/use-polling';
import { ReadFeedback } from '../../components/intake/status';
import { MoneyValue } from '../../components/shared/format-value';
import { TableRegion } from '../../components/shared/workspace';

const manual = (_page: ReviewQueuePage) => false;
export default function AuditIndex() {
  const router = useRouter();
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((signal: AbortSignal) => api.reviewList({ outcome: 'all', offset, limit: 20 }, signal), [offset]);
  const read = usePolling(load, manual);
  function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = String(new FormData(event.currentTarget).get('invoice_id') ?? '').trim();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
      setError('Enter a valid invoice case UUID.'); return;
    }
    setError(null); router.push(`/audit/${id}`);
  }
  const page = read.data;
  return <main className="audit-workspace"><h1>Invoice audit</h1>
    <p>Manager-only read access to recorded evidence, versions and differences. Missing historical identities and times remain unknown.</p>
    <form onSubmit={open} className="audit-open"><label htmlFor="audit-invoice-id">Invoice case UUID</label>
      <input id="audit-invoice-id" name="invoice_id" required aria-invalid={!!error} aria-describedby={error ? 'audit-id-error' : undefined} />
      {error && <p id="audit-id-error" role="alert">{error}</p>}<button type="submit">Open invoice audit</button></form>
    <ReadFeedback loading={read.loading} error={read.error} paused={read.paused} refresh={read.refresh} />
    {page && <><p>{page.total} cases across all automatic outcomes, including incomplete processing</p>
      <TableRegion label="Choose an invoice audit case"><table><caption>Choose a case to inspect</caption><thead><tr>
        <th>Invoice</th><th>Supplier</th><th>Amount</th><th>Automatic outcome</th><th>Operational state</th>
      </tr></thead><tbody>{page.items.map(item => <tr key={item.invoice_id}>
        <td><Link href={`/audit/${item.invoice_id}`}>{item.invoice_number ?? item.invoice_id}</Link></td>
        <td>{displayValue(item.supplier_name)}</td><td><MoneyValue amount={item.total_amount} currency={item.currency} /></td>
        <td>{item.automatic_outcome ?? 'Incomplete — no final outcome'}</td><td>{item.operational_state.replaceAll('_', ' ')}</td>
      </tr>)}</tbody></table></TableRegion>
      {!page.items.length && <p role="status">No invoice cases yet.</p>}
      <nav aria-label="Audit case pages"><button disabled={read.loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous cases</button>
        <button disabled={read.loading || offset + page.limit >= page.total} onClick={() => setOffset(offset + page.limit)}>Next cases</button></nav>
    </>}
  </main>;
}
