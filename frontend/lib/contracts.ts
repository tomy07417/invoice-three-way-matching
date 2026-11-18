// Public FastAPI contracts. Dates are ISO strings; Decimal values are never JS numbers.
export type DecimalString = string;
export type ProcessingState = 'QUEUED' | 'EXTRACTING' | 'EXTRACTED' | 'EVALUATING'
  | 'COMPLETED' | 'RETRY_WAIT' | 'EXTRACTION_FAILED' | 'EVALUATION_INCOMPLETE' | 'PROCESSING_FAILED';
export type AutomaticOutcome = 'Auto-Pay' | 'Rejected' | 'Held for Review';
export interface Session {
  actor: { actor_id: number; username: string; role: 'intake' | 'ap_analyst' | 'ap_manager' };
  csrf_token: string;
  expires_at: string;
}
export interface ProcessingSnapshot {
  invoice_id: string;
  job_id: string;
  processing_status: ProcessingState;
  attempts: number;
  next_eligible_at: string;
  created_at: string;
  updated_at: string;
  automatic_outcome: AutomaticOutcome | null;
  error: { code: string; message: string; retryable: boolean } | null;
}
export interface InvoiceSummary extends ProcessingSnapshot {
  invoice_number: string | null;
  supplier_name: string | null;
  invoice_date: string | null;
  total_amount: DecimalString | null;
  currency: string | null;
}
export interface InvoicePage { items: InvoiceSummary[]; total: number; offset: number; limit: number }
export interface ListOptions { offset?: number; limit?: number; status?: ProcessingState }
export interface InvoiceLine {
  description: string | null;
  quantity: DecimalString | null;
  unit_price: DecimalString | null;
  line_total: DecimalString | null;
  po_line_reference: string | null;
  po_number: string | null;
  po_line_number: number | null;
  unit_price_basis: 'net' | 'gross' | 'unknown';
}
export interface InvoiceDraft {
  supplier_name: string | null;
  supplier_tax_id: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  po_references: string[] | null;
  currency: string | null;
  subtotal: DecimalString | null;
  tax_amount: DecimalString | null;
  total_amount: DecimalString | null;
  lines: InvoiceLine[];
}
export interface Finding {
  code: string;
  outcome: 'PASS' | 'REJECT' | 'HOLD' | 'PENDING';
  reason: string;
  po_number?: string | null;
  line_index?: number | null;
  po_line_number?: number | null;
}
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export interface Evaluation {
  invoice_id: number;
  configuration_id: number;
  evaluated_at: string;
  actor_id: string;
  origin: string;
  evaluation_id: number | null;
  result: {
    provisional_status: AutomaticOutcome;
    final_status: AutomaticOutcome | null;
    rules: Finding[];
    config: { price_tolerance: DecimalString; analyst_approval_limit: DecimalString };
  };
  input_snapshot: Record<string, JsonValue>;
  context_snapshot: Record<string, JsonValue>;
}
export interface InvoiceDetail extends ProcessingSnapshot {
  document: {
    file_name: string; media_type: 'application/pdf' | 'image/png' | 'image/jpeg';
    byte_size: number; sha256: string; page_count: number;
    pixel_width: number | null; pixel_height: number | null;
  };
  extraction: { is_invoice: boolean; invoice: InvoiceDraft | null; warnings: string[] } | null;
  evaluation: Evaluation | null;
  findings: Finding[];
}
export interface ProcessingDetail extends ProcessingSnapshot {
  attempt_history: Record<string, JsonValue>[];
  events: Record<string, JsonValue>[];
}
