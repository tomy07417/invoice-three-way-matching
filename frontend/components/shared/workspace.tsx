'use client';

import type { FormEventHandler, ReactNode, Ref } from 'react';
import { api } from '../../lib/api';
import type { DocumentValidation } from '../../lib/contracts';
import './workspace.css';

/** Parent supplies the native table (including its caption, thead, th scopes and tbody). */
export function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  return <div className="shared-table-region" role="region" aria-label={label} tabIndex={0}>{children}</div>;
}

export function FilterForm({ legend, onSubmit, children, disabled = false }: {
  legend: string; onSubmit: FormEventHandler<HTMLFormElement>; children: ReactNode; disabled?: boolean;
}) {
  return <form className="shared-filters" aria-label={legend} onSubmit={onSubmit}>
    <fieldset disabled={disabled}><legend>{legend}</legend>{children}</fieldset>
  </form>;
}

/** Caller supplies explicit labels/text; color never determines business meaning. */
export function StatusBadge({ label, tone = 'neutral' }: {
  label: string; tone?: 'neutral' | 'positive' | 'warning' | 'negative';
}) {
  return <span className={`shared-status shared-status--${tone}`}>{label}</span>;
}

export function EvidencePanel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="shared-evidence" aria-label={title}><h3>{title}</h3>{children}</section>;
}

/** Deterministic pre-matching disposition, never presented as an invoice evaluation. */
export function DocumentValidationNotice({ validation }: { validation?: DocumentValidation }) {
  if (!validation) return null;
  return <EvidencePanel title="Document validation">
    <p>Document type: <strong>{validation.document_type}</strong>{' · '}
      <StatusBadge label={validation.automatic_outcome} tone={validation.automatic_outcome === 'Rejected' ? 'negative' : 'warning'} /></p>
    <ul>{validation.findings.map(finding => <li key={finding.code}>
      {finding.reason} <small>Rule: {finding.code}</small>
    </li>)}</ul>
    <p>Invoice field edits cannot change document classification. Review the original document; upload a confirmed invoice if necessary.</p>
  </EvidencePanel>;
}

/** Semantic chronology; parent retains recorded ordering and supplies actor/time/evidence content. */
export function Timeline({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  return <ol className={`shared-timeline ${className}`} aria-label={label}>{children}</ol>;
}

export function TimelineEvent({ title, metadata, children }: {
  title: string; metadata: ReactNode; children?: ReactNode;
}) {
  return <li className="shared-timeline-event"><h3>{title}</h3>
    <div className="shared-timeline-metadata">{metadata}</div>{children}</li>;
}

/** Fully controlled: no submission, validation policy or comment clearing lives here. */
export function CommentField({ id, value, onChange, label = 'Comment', required = true,
  help, error, feedback, disabled = false, rows = 4, inputRef, maxLength = 4000 }: {
  id: string; value: string; onChange: (value: string) => void; label?: string; required?: boolean;
  help?: string; error?: string; feedback?: string; disabled?: boolean; rows?: number;
  inputRef?: Ref<HTMLTextAreaElement>; maxLength?: number;
}) {
  const describedBy = [help && `${id}-help`, error && `${id}-error`, feedback && `${id}-feedback`]
    .filter(Boolean).join(' ') || undefined;
  return <div className="shared-comment">
    <label htmlFor={id}>{label}{required ? ' (required)' : ' (optional)'}</label>
    {help && <p id={`${id}-help`}>{help}</p>}
    <textarea ref={inputRef} maxLength={maxLength} id={id} name={id} value={value} onChange={event => onChange(event.target.value)}
      required={required} disabled={disabled} rows={rows} aria-invalid={error ? true : undefined}
      aria-describedby={describedBy} />
    {error && <p className="shared-field-error" id={`${id}-error`} role="alert">{error}</p>}
    {feedback && <p id={`${id}-feedback`} role="status">{feedback}</p>}
  </div>;
}

/** Same-origin authenticated stream only. API validates the UUID; no URL/srcDoc escape hatch.
 * Authorization/expiry remain enforced by the backend, not by this visual component.
 * Native PDF rendering requires an unsandboxed frame. The document proxy restricts successful
 * responses to PDF/PNG/JPEG with nosniff; the authenticated link remains the fallback.
 */
export function OriginalDocument({ invoiceId, title = 'Original invoice', preview = false,
  openLabel = 'Open authenticated original', download }: {
  invoiceId: string; title?: string; preview?: boolean; openLabel?: string; download?: string | true;
}) {
  const source = api.documentURL(invoiceId);
  return <section className="shared-document" aria-label={title}>
    <h3>{title}</h3>
    <a href={source} target="_blank" rel="noopener noreferrer">{openLabel}</a>
    {download !== undefined && <>{' · '}<a href={source} download={download}>Download original</a></>}
    <p>Access requires an authorized session. Preview availability depends on your browser.</p>
    {preview && <iframe title={title} src={source} referrerPolicy="same-origin" />}
  </section>;
}
