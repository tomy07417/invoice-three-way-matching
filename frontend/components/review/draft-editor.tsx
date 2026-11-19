'use client';

import { useRef, useState, type FormEvent } from 'react';
import type { InvoiceDraft, InvoiceLine } from '../../lib/contracts';
import { correctionChanges, draftFromForm, HEADER_LABELS, LINE_LABELS, requiredReviewComment, reviewError } from '../../lib/review-state';

const emptyLine: InvoiceLine = { description: null, quantity: null, unit_price: null, line_total: null,
  po_line_reference: null, po_number: null, po_line_number: null, unit_price_basis: 'unknown' };
export function DraftEditor({ initial, busy, onSave, onCancel, onDirty }: {
  initial: InvoiceDraft | null; busy: boolean; onSave: (draft: InvoiceDraft, comment: string) => void;
  onCancel: () => void; onDirty: () => void;
}) {
  const form = useRef<HTMLFormElement>(null);
  const nextKey = useRef(initial?.lines.length ?? 0);
  const [rows, setRows] = useState(() => (initial?.lines ?? []).map((value, key) => ({ key, value })));
  const [unknownPO, setUnknownPO] = useState(initial?.po_references === null || initial === null);
  const [preview, setPreview] = useState<InvoiceDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  function prepare() {
    if (!form.current?.reportValidity()) return;
    try {
      const values = new FormData(form.current);
      requiredReviewComment(String(values.get('comment') ?? ''));
      const draft = draftFromForm(values, rows.length, initial, rows.map(row => row.key));
      if (!correctionChanges(initial, draft).length) throw new Error('Change invoice data before saving a correction.');
      setPreview(draft); setError(null);
    } catch (failure) { setError(reviewError(failure)); }
  }
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview || busy) return;
    try {
      const values = new FormData(event.currentTarget);
      onSave(draftFromForm(values, rows.length, initial, rows.map(row => row.key)), requiredReviewComment(String(values.get('comment') ?? '')));
    } catch (failure) { setError(reviewError(failure)); }
  }
  const changes = preview ? correctionChanges(initial, preview) : [];
  return <section className="panel" aria-label="Extraction correction"><h2>Correct extracted data</h2>
    <p>These are invoice facts, not supplier master, PO or GRN edits. Saving revokes any prior authorization and releases its quantities with audit. Reevaluation is required; a complete Auto-Pay result authorizes payment automatically unless blocked. No bank payment is executed.</p>
    <form ref={form} onSubmit={save} onInput={() => { onDirty(); setPreview(null); }}>
      <fieldset disabled={busy}><legend>Invoice headers</legend><div className="review-form-grid">
        {Object.entries(HEADER_LABELS).map(([key, label]) => <label key={key} htmlFor={`edit-${key}`}>{label}
          <input id={`edit-${key}`} name={key} type={key.endsWith('_date') ? 'date' : 'text'} maxLength={500}
            defaultValue={initial?.[key as keyof typeof HEADER_LABELS] ?? ''} />
        </label>)}
        <label htmlFor="edit-po-references">PO references, comma separated<input id="edit-po-references" name="po_references"
          defaultValue={initial?.po_references?.join(', ') ?? ''} disabled={unknownPO} /></label>
        <label className="review-checkbox"><input name="po_references_unknown" type="checkbox" checked={unknownPO}
          onChange={event => { setUnknownPO(event.target.checked); setPreview(null); onDirty(); }} />PO references not yet known</label>
      </div></fieldset>
      <fieldset disabled={busy}><legend>Invoice lines and explicit PO associations</legend>
        {rows.map((row, index) => <fieldset key={row.key} className="review-line"><legend>Line {index + 1}</legend>
          <div className="review-form-grid">{Object.entries(LINE_LABELS).map(([key, label]) => <label key={key} htmlFor={`line-${index}-${key}`}>{label}
            {key === 'unit_price_basis' ? <select id={`line-${index}-${key}`} name={`line-${index}-${key}`} defaultValue={row.value.unit_price_basis}>
              <option value="unknown">Unknown</option><option value="net">Net of tax</option><option value="gross">Gross</option>
            </select> : <input id={`line-${index}-${key}`} name={`line-${index}-${key}`} maxLength={key === 'description' ? 2000 : 500}
              defaultValue={row.value[key as keyof InvoiceLine] ?? ''} />}
          </label>)}</div>
          <button type="button" onClick={() => { setRows(value => value.filter(item => item.key !== row.key)); setPreview(null); onDirty(); }}>Remove line {index + 1}</button>
        </fieldset>)}
        <button type="button" disabled={rows.length >= 500} onClick={() => {
          setRows(value => [...value, { key: nextKey.current++, value: { ...emptyLine } }]); setPreview(null); onDirty();
        }}>Add invoice line</button>
      </fieldset>
      <label htmlFor="correction-comment">Correction reason (required)<textarea id="correction-comment" name="comment" required maxLength={4000} disabled={busy} /></label>
      {error && <p role="alert">{error}</p>}
      <div className="review-actions"><button type="button" onClick={prepare} disabled={busy}>Preview correction</button>
        <button type="button" onClick={onCancel} disabled={busy}>Cancel correction</button></div>
      {preview && <><div className="table-scroll"><table><caption>Current version versus proposed correction</caption>
        <thead><tr><th>Field</th><th>Current version</th><th>Proposed correction</th></tr></thead>
        <tbody>{changes.map(change => <tr key={change.field}><th>{change.field}</th><td>{change.before}</td><td>{change.after}</td></tr>)}</tbody>
      </table></div><button type="submit" disabled={busy}>Save correction</button></>}
    </form>
  </section>;
}
