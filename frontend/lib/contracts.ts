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
export interface AccountingEvidence {
  taxes_separately_stated: boolean | null;
  adjustments: { kind: 'discount' | 'charge' | 'rounding' | 'unknown';
    stage: 'before_subtotal' | 'after_subtotal' | 'unknown';
    amount: DecimalString | null; line_index: number | null }[] | null;
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
  accounting_evidence?: AccountingEvidence | null;
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

export interface RuleValues { price_tolerance: DecimalString; analyst_approval_limit: DecimalString }
export interface ParameterVersion extends RuleValues {
  version_id: number; currency: 'USD'; effective_from: string;
  actor: { actor_id: number; username: string; role: string } | null;
  actor_reference: string;
}
export interface ParameterChange extends ParameterVersion {
  previous_version_id: number | null; before: RuleValues | null; after: RuleValues;
  comment: string | null; provenance: 'manager_change' | 'bootstrap' | 'historical_unknown';
}
export interface ParameterHistory { items: ParameterChange[]; total: number; offset: number; limit: number }
export interface ParameterUpdate { price_tolerance?: DecimalString; analyst_approval_limit?: DecimalString; comment?: string | null }
export type AuditKind = 'creation' | 'extraction' | 'evaluation' | 'correction' | 'reevaluation'
  | 'decision' | 'escalation' | 'credit_note_request' | 'state_change' | 'approval_reversal';
export interface AuditFilters {
  offset?: number; limit?: number; kind?: AuditKind; actor_id?: string; time_from?: string; time_to?: string;
}
export interface AuditEvent {
  id: string; kind: AuditKind; at: string | null;
  actor: { id: string | null; name: string | null; role: string | null; type: 'recorded' | 'system' | 'unknown' };
  provenance: { source: string; source_id: string; derived: boolean; note: string | null };
  revision: number | null; extraction_version: number | null; evaluation_id: number | null;
  current: boolean; comment: string | null;
  diffs: { field: string; before: JsonValue; after: JsonValue; before_known: boolean; derived: boolean; source: string }[];
  evidence: Record<string, JsonValue>;
}
export interface AuditHistory { invoice_id: string; revision: number; events: AuditEvent[]; total: number; offset: number; limit: number }

export type ReviewState = 'REVIEW_REQUIRED' | 'REEVALUATION_REQUIRED' | 'ESCALATED' | 'APPROVED' | 'REJECTED' | 'CREDIT_NOTE_REQUESTED';
export type ReviewAction = 'approve' | 'reject' | 'request_credit_note';
export interface ApprovalEligibility {
  allowed: boolean; requires_manager: boolean; reason: string | null;
  limit_amount: DecimalString; limit_currency: 'USD';
}
export interface ComparisonFinding {
  rule: string; message: string;
  // Backend comparison_rows projects locators/outcome only, not code/reason.
  evidence: { outcome: Finding['outcome']; po_number: string | null;
    line_index: number | null; po_line_number: number | null };
}
export interface ComparisonRow {
  line_index: number; description: string | null;
  quantity_invoiced: DecimalString | null; po_number: string | null; po_line_number: number | null;
  quantity_ordered: DecimalString | null; quantity_received: DecimalString | null;
  quantity_previously_approved: DecimalString | null; quantity_available: DecimalString | null;
  po_net_unit_price: DecimalString | null; invoice_net_unit_price: DecimalString | null;
  invoice_currency: string | null; po_currency: string | null; tolerance: DecimalString | null;
  association_status: 'explicit' | 'unresolved'; findings: ComparisonFinding[];
  declared_unit_price_basis?: 'net' | 'gross' | 'unknown';
  effective_unit_price_basis?: 'net' | 'gross' | 'unknown';
  unit_price_basis_source?: 'declared' | 'arithmetic' | 'undetermined';
  price_basis_reason?: string;
}
export interface ReviewDetail {
  invoice_id: string; revision: number; extraction_version: number;
  extraction: InvoiceDraft | null; original_extraction: InvoiceDraft | null;
  evaluation: Evaluation | null; comparison_rows: ComparisonRow[];
  operational_state: ReviewState; payment_blocked: boolean; credit_note_pending: boolean;
  requires_reevaluation: boolean; eligibility: ApprovalEligibility;
  current_decision: { id: number; revision: number; extraction_version: number; evaluation_id: number | null;
    action: ReviewAction; actor_id: string; at: string; comment: string } | null;
  document_url: string; created_at: string; updated_at: string;
}
export interface ReviewSummary {
  invoice_id: string; revision: number; invoice_number: string | null; supplier_name: string | null;
  invoice_date: string | null; total_amount: DecimalString | null; currency: string | null;
  operational_state: ReviewState; payment_blocked: boolean; automatic_outcome: AutomaticOutcome | null;
  reasons: string[]; requires_manager: boolean; created_at: string; age_seconds: number;
}
export interface ReviewQueuePage { items: ReviewSummary[]; total: number; offset: number; limit: number }
export interface ReviewListOptions {
  offset?: number; limit?: number; supplier?: string; date_from?: string; date_to?: string;
  min_amount?: DecimalString; max_amount?: DecimalString; reason?: string; operational_state?: ReviewState;
  min_age_days?: number; sort?: 'created_at' | 'age' | 'amount' | 'supplier' | 'invoice_date';
  direction?: 'asc' | 'desc'; outcome?: AutomaticOutcome | 'all';
}
export interface ReviewHistoryEvent {
  kind: string; at: string; actor_id: string; actor_name?: string; actor_role?: string;
  comment: string; current: boolean; revision?: number; extraction_version?: number;
  evaluation_id?: number | null; decision_id?: number; action?: ReviewAction;
  evaluation?: Evaluation | null; invoice?: InvoiceDraft | null;
  [key: string]: unknown;
}
export interface ReviewHistory { invoice_id: string; revision: number; events: ReviewHistoryEvent[] }
