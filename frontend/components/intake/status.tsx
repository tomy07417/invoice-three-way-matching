import type { ProcessingSnapshot } from '../../lib/contracts';
import { outcomeLabel } from '../../lib/intake-state';
import { TimestampValue } from '../shared/format-value';
import { StatusBadge } from '../shared/workspace';

export function InvoiceStatus({ invoice }: { invoice: ProcessingSnapshot }) {
  return <section aria-label="Processing status" className="panel">
    <h2>Processing</h2>
    <dl><dt>Technical state</dt><dd><StatusBadge label={invoice.processing_status} /></dd>
      <dt>Automatic outcome</dt><dd><StatusBadge label={outcomeLabel(invoice.processing_status, invoice.automatic_outcome)} /></dd>
      <dt>Attempts</dt><dd>{invoice.attempts}</dd>
      <dt>Next eligible time</dt><dd><TimestampValue value={invoice.next_eligible_at} /></dd>
      <dt>Last updated</dt><dd><TimestampValue value={invoice.updated_at} /></dd></dl>
    {invoice.processing_status === 'EVALUATION_INCOMPLETE' && <p>Mandatory evidence is incomplete. The result is unknown, not an approval or rejection.</p>}
    {invoice.error && <p role="status">Technical issue: {invoice.error.message} ({invoice.error.code}). This is not a business rejection.</p>}
  </section>;
}
export function ReadFeedback({ loading, error, paused, refresh }: {
  loading: boolean; error: string | null; paused: string | null; refresh: () => void;
}) {
  return <div className="read-feedback">
    <button type="button" onClick={refresh} disabled={loading}>Refresh</button>
    {loading && <p role="status">Loading invoice data…</p>}
    {error && <p role="alert">{error}</p>}
    {paused && <p role="status">{paused}</p>}
  </div>;
}
