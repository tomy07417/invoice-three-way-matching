'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useRef, useState } from 'react';
import { api } from '../../../lib/api';
import type { InvoiceDetail, InvoiceDraft } from '../../../lib/contracts';
import { displayValue, errorMessage, isActive, isCancelled } from '../../../lib/intake-state';
import { useSession } from '../../../components/intake/session-gate';
import { usePolling } from '../../../components/intake/use-polling';
import { InvoiceStatus, ReadFeedback } from '../../../components/intake/status';
import { MoneyValue } from '../../../components/shared/format-value';
import { OriginalDocument, TableRegion } from '../../../components/shared/workspace';

const activeDetail = (invoice: InvoiceDetail) => isActive(invoice.processing_status);
const HEADERS: [keyof Omit<InvoiceDraft, 'lines' | 'po_references'>, string][] = [
  ['supplier_name', 'Supplier name'], ['supplier_tax_id', 'Supplier tax ID'], ['invoice_number', 'Invoice number'],
  ['invoice_date', 'Invoice date'], ['due_date', 'Due date'], ['currency', 'Currency'], ['subtotal', 'Subtotal'],
  ['tax_amount', 'Tax amount'], ['total_amount', 'Total amount'],
];
export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { session, handleError } = useSession();
  const retryInFlight = useRef(false);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [retryMessage, setRetryMessage] = useState<string | null>(null);
  const load = useCallback(async (signal: AbortSignal) => {
    // Read job progress first, then the complete detail. Never stamp a newer terminal
    // status onto older extraction/findings: that could stop polling with stale evidence.
    await api.processing(id, signal);
    return api.detail(id, signal);
  }, [id]);
  const read = usePolling(load, activeDetail);
  const invoice = read.data;
  const draft = invoice?.extraction?.invoice;
  async function confirmRetry() {
    if (retryInFlight.current || !invoice || !isActive(invoice.processing_status)) return;
    retryInFlight.current = true;
    setRetrying(true);
    setRetryError(null);
    setRetryMessage(null);
    try {
      const snapshot = await api.retry(id, session.csrf_token);
      setRetryMessage(`Already-scheduled work confirmed: ${snapshot.processing_status}. No attempts or eligibility times were reset.`);
      read.refresh();
    } catch (failure) {
      if (!isCancelled(failure)) { handleError(failure); setRetryError(errorMessage(failure)); }
    } finally { retryInFlight.current = false; setRetrying(false); }
  }
  return <main><p><Link href="/invoices">Back to invoices</Link></p><h1>Invoice detail</h1><p className="identifier">{id}</p>
    <ReadFeedback {...read} />
    {invoice && <>
      <InvoiceStatus invoice={invoice} />
      {isActive(invoice.processing_status) && <section className="panel" aria-label="Scheduled processing">
        <p>Processing is already scheduled. This control only confirms existing active work; it does not restart terminal jobs, reset attempts or bring forward the next eligible time.</p>
        <button onClick={confirmRetry} disabled={retrying}>{retrying ? 'Confirming…' : 'Confirm scheduled retry'}</button>
      </section>}
      {retryError && <p role="alert">{retryError}</p>}{retryMessage && <p role="status">{retryMessage}</p>}
      <section className="panel"><h2>Original document</h2><p>{invoice.document.file_name} — {invoice.document.media_type}, {invoice.document.byte_size} bytes, {invoice.document.page_count} page(s)</p>
        <OriginalDocument invoiceId={id} openLabel="View original" download={invoice.document.file_name} />
      </section>
      <section className="panel"><h2>Extracted headers</h2>
        {!draft ? <p>No extracted invoice fields are available yet.</p> : <dl>
          {HEADERS.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{['subtotal', 'tax_amount', 'total_amount'].includes(key)
            ? <MoneyValue amount={draft[key]} currency={draft.currency} /> : displayValue(draft[key])}</dd></div>)}
          <dt>PO references</dt><dd>{draft.po_references?.length ? draft.po_references.join(', ') : '—'}</dd>
        </dl>}
      </section>
      <section className="panel"><h2>Extraction warnings</h2>
        {invoice.extraction?.warnings.length ? <ul>{invoice.extraction.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : <p>No warnings reported.</p>}
      </section>
      <section><h2>Extracted lines</h2>{!draft?.lines.length ? <p>No extracted lines available.</p> :
        <TableRegion label="Extracted invoice lines"><table><caption>Extracted lines — monetary values retain source precision</caption>
          <thead><tr>{['Line', 'Description', 'Quantity', 'Unit price', 'Price basis', 'Line total', 'PO line reference', 'PO number', 'PO line number'].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{draft.lines.map((line, index) => <tr key={index}><th scope="row">{index + 1}</th>
            <td>{displayValue(line.description)}</td><td>{displayValue(line.quantity)}</td><td><MoneyValue amount={line.unit_price} currency={draft.currency} /></td><td>{line.unit_price_basis}</td>
            <td><MoneyValue amount={line.line_total} currency={draft.currency} /></td><td>{displayValue(line.po_line_reference)}</td><td>{displayValue(line.po_number)}</td><td>{displayValue(line.po_line_number)}</td>
          </tr>)}</tbody></table></TableRegion>}
      </section>
      <section className="panel"><h2>Reasons and evidence</h2>
        <p>Technical state and automatic business outcome are separate. No payment or human decision is made here.</p>
        {invoice.findings.length ? <ul className="findings">{invoice.findings.map((finding, index) => <li key={index}>
          <strong>{finding.outcome}</strong>: {finding.reason}<small>Rule: {finding.code}
            {' · '}Invoice line index: {displayValue(finding.line_index)}{' · '}PO: {displayValue(finding.po_number)}{' · '}PO line: {displayValue(finding.po_line_number)}</small>
        </li>)}</ul> : <p>No evaluation findings available yet.</p>}
      </section>
    </>}
  </main>;
}
