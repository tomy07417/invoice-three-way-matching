import type { InvoiceDraft, InvoiceLine, ReviewAction } from './contracts';

export const REVIEW_STATES = ['REVIEW_REQUIRED', 'REEVALUATION_REQUIRED', 'ESCALATED', 'APPROVED', 'REJECTED', 'CREDIT_NOTE_REQUESTED'] as const;
export const HEADER_LABELS = {
  supplier_name: 'Supplier name', supplier_tax_id: 'Supplier tax ID', invoice_number: 'Invoice number',
  invoice_date: 'Invoice date', due_date: 'Due date', currency: 'Currency',
  subtotal: 'Subtotal', tax_amount: 'Tax amount', total_amount: 'Total amount',
} as const;
export const LINE_LABELS = {
  description: 'Description', quantity: 'Quantity', unit_price: 'Unit price', line_total: 'Line total',
  po_line_reference: 'Original PO line reference', po_number: 'PO number', po_line_number: 'PO line number',
  unit_price_basis: 'Price basis',
} as const;
const decimalFields = new Set(['subtotal', 'tax_amount', 'total_amount', 'quantity', 'unit_price', 'line_total']);

export function currentDecisionLabel(actorId: string): string {
  return actorId === 'system:auto-pay' ? 'Current automatic decision' : 'Current human decision';
}
export function isAP(role: string): boolean { return role === 'ap_analyst' || role === 'ap_manager'; }
export function decisionConfirmation(action: ReviewAction): string {
  const messages: Record<ReviewAction, string> = {
    approve: 'Confirm payment authorization against the current evidence? No bank payment will be executed.',
    reject: 'Reject invoice with the recorded comment? The original automatic outcome remains unchanged.',
    request_credit_note: 'Request credit note and block payment authorization? This records an operational hold, not a bank payment or supplier notification.',
  };
  return messages[action];
}
export function requiredReviewComment(value: string): string {
  if (!value.trim() || value.length > 4000) throw new Error('A nonblank comment of at most 4,000 characters is required.');
  return value.trim();
}
export function reviewError(error: unknown): string {
  const failure = error as { status?: number; message?: string } | null;
  if (failure?.status === 401) return 'Your session expired. Sign in again to continue.';
  if (failure?.status === 409 || failure?.status === 428) return `${failure.message ?? 'The case version changed.'} Refresh and review current evidence before retrying.`;
  return failure?.message ?? 'The service could not be reached. Refresh the case before retrying an uncertain decision.';
}

export function draftFromForm(form: FormData, lineCount: number, original?: InvoiceDraft | null,
  lineKeys?: number[]): InvoiceDraft {
  function value(name: string, field: string): string | null {
    const raw = String(form.get(name) ?? '').trim();
    if (!raw) return null;
    if (decimalFields.has(field) && (raw.length > 100 || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw))) {
      throw new Error(`${field.replaceAll('_', ' ')} must be an exact decimal, without commas or nonfinite values.`);
    }
    return raw;
  }
  const header = Object.fromEntries(Object.keys(HEADER_LABELS).map(key => [key, value(key, key)]));
  if (header.currency && !/^[A-Z]{3}$/.test(header.currency)) throw new Error('Currency must be three uppercase letters.');
  const poReferences = String(form.get('po_references') ?? '').split(',').map(item => item.trim()).filter(Boolean);
  const lines: InvoiceLine[] = [];
  if (!Number.isInteger(lineCount) || lineCount < 0 || lineCount > 500) throw new Error('At most 500 invoice lines are supported.');
  for (let index = 0; index < lineCount; index++) {
    const line = Object.fromEntries(Object.keys(LINE_LABELS).map(key => [key, value(`line-${index}-${key}`, key)]));
    const rawNumber = line.po_line_number;
    if (rawNumber !== null && (!/^\d+$/.test(rawNumber) || Number(rawNumber) < 1 || Number(rawNumber) > 2147483647)) {
      throw new Error(`Line ${index + 1} PO line number must be a positive integer.`);
    }
    if (!['net', 'gross', 'unknown'].includes(line.unit_price_basis ?? '')) throw new Error('Choose a valid price basis.');
    lines.push({ ...line, po_line_number: rawNumber === null ? null : Number(rawNumber) } as InvoiceLine);
  }
  const draft = { ...header, po_references: form.has('po_references_unknown') ? null : poReferences, lines } as InvoiceDraft;
  if (original?.accounting_evidence !== undefined) {
    // Stored line-scoped facts cannot silently move to another line after removal/addition.
    const sameLines = lineCount === original.lines.length && (!lineKeys || lineKeys.every((key, index) => key === index));
    draft.accounting_evidence = sameLines ? structuredClone(original.accounting_evidence) : null;
  }
  return draft;
}

export interface CorrectionChange { field: string; before: string; after: string }
export function correctionChanges(original: InvoiceDraft | null, proposed: InvoiceDraft): CorrectionChange[] {
  const changes: CorrectionChange[] = [];
  const display = (value: unknown) => value === null || value === undefined ? 'Unknown'
    : Array.isArray(value) ? value.join(', ') || 'None' : String(value);
  const add = (field: string, before: unknown, after: unknown) => {
    if (JSON.stringify(before ?? null) !== JSON.stringify(after ?? null)) changes.push({ field, before: display(before), after: display(after) });
  };
  for (const [key, label] of Object.entries(HEADER_LABELS)) add(label, original?.[key as keyof typeof HEADER_LABELS], proposed[key as keyof typeof HEADER_LABELS]);
  add('PO references', original?.po_references, proposed.po_references);
  const count = Math.max(original?.lines.length ?? 0, proposed.lines.length);
  for (let index = 0; index < count; index++) {
    for (const [key, label] of Object.entries(LINE_LABELS)) {
      add(`Line ${index + 1} · ${label}`, original?.lines[index]?.[key as keyof InvoiceLine], proposed.lines[index]?.[key as keyof InvoiceLine]);
    }
  }
  return changes;
}
