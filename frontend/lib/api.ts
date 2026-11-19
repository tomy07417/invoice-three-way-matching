import type { InvoiceDetail, InvoiceDraft, InvoicePage, ListOptions, ProcessingDetail, ProcessingSnapshot, Session,
  ReviewAction, ReviewDetail, ReviewHistory, ReviewListOptions, ReviewQueuePage,
  ParameterVersion, ParameterHistory, ParameterUpdate, AuditHistory, AuditFilters } from './contracts';

const ROOT = '/api/backend';
export const MAX_INVOICE_BYTES = 8 * 1024 * 1024;
export const INVOICE_MEDIA_TYPES = ['application/pdf', 'image/png', 'image/jpeg'] as const;

/** Technical transport failure, never an automatic business outcome. */
export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string,
    public readonly retryable: boolean, public readonly correlationId: string | null = null) {
    super(message);
    this.name = 'ApiError';
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function parse(text: string): unknown {
  try { return JSON.parse(text); } catch { return undefined; }
}
function httpError(status: number, data: unknown, correlationId: string | null): ApiError {
  const messages: Record<number, string> = {
    401: 'Authentication required', 403: 'Access denied', 413: 'Invoice is too large',
    415: 'Unsupported document type', 422: 'Invalid request parameters',
    429: 'Too many requests; try again later',
  };
  if (record(data) && record(data.error)) {
    const error = data.error;
    if (typeof error.code === 'string' && typeof error.message === 'string'
      && typeof error.retryable === 'boolean') {
      return new ApiError(status, error.code, error.message, error.retryable,
        typeof error.correlation_id === 'string' ? error.correlation_id : correlationId);
    }
  }
  // Sessions use FastAPI's detail contract, unlike the explicit safe error envelopes.
  // Never reflect arbitrary upstream detail strings (or validation input/tracebacks).
  const sessionMessages: Record<number, string[]> = {
    401: ['Authentication required', 'Invalid username or password'],
    403: ['CSRF validation failed', 'Forbidden'],
    503: ['Account service unavailable'],
  };
  const message = record(data) && typeof data.detail === 'string' && sessionMessages[status]?.includes(data.detail)
    ? data.detail : messages[status] ?? (status >= 500 ? 'Service temporarily unavailable' : 'Request failed');
  return new ApiError(status, `http_${status}`, message, status === 429 || status >= 500, correlationId);
}
type ResponseGuard = (value: unknown) => boolean;
const nullableString = (value: unknown) => value === null || typeof value === 'string';
const integer = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const nullableInteger = (value: unknown) => value === null || integer(value);
const decimal = (value: unknown) => typeof value === 'string'
  && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value);
const nullableDecimal = (value: unknown) => value === null || decimal(value);
const strings = (value: unknown) => Array.isArray(value) && value.every(item => typeof item === 'string');
const uuid = (value: unknown) => typeof value === 'string'
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const documentType = (value: unknown) => typeof value === 'string' && ['invoice', 'quotation', 'purchase_order',
  'delivery_note', 'credit_note', 'receipt', 'statement', 'other', 'unknown'].includes(value);
const processingState = (value: unknown) => typeof value === 'string' && ['QUEUED', 'EXTRACTING', 'EXTRACTED',
  'EVALUATING', 'COMPLETED', 'RETRY_WAIT', 'EXTRACTION_FAILED', 'EVALUATION_INCOMPLETE', 'PROCESSING_FAILED'].includes(value);
const outcome = (value: unknown) => typeof value === 'string' && ['Auto-Pay', 'Rejected', 'Held for Review'].includes(value);
const nullableOutcome = (value: unknown) => value === null || outcome(value);
const reviewState = (value: unknown) => typeof value === 'string' && ['REVIEW_REQUIRED', 'REEVALUATION_REQUIRED',
  'ESCALATED', 'APPROVED', 'REJECTED', 'CREDIT_NOTE_REQUESTED'].includes(value);
const reviewAction = (value: unknown) => typeof value === 'string' && ['approve', 'reject', 'request_credit_note'].includes(value);
const ruleOutcome = (value: unknown) => typeof value === 'string' && ['PASS', 'REJECT', 'HOLD', 'PENDING'].includes(value);
const session: ResponseGuard = value => record(value) && record(value.actor) && integer(value.actor.actor_id)
  && typeof value.actor.username === 'string' && typeof value.actor.role === 'string'
  && ['intake', 'ap_analyst', 'ap_manager'].includes(value.actor.role)
  && typeof value.csrf_token === 'string' && typeof value.expires_at === 'string';
const processingSnapshot: ResponseGuard = value => record(value) && uuid(value.invoice_id) && uuid(value.job_id)
  && processingState(value.processing_status) && integer(value.attempts) && typeof value.next_eligible_at === 'string'
  && typeof value.created_at === 'string' && typeof value.updated_at === 'string' && nullableOutcome(value.automatic_outcome)
  && (value.document_validation === undefined || record(value.document_validation) && documentValidation(value.document_validation)
    && value.automatic_outcome === value.document_validation.automatic_outcome)
  && (value.error === null || record(value.error) && typeof value.error.code === 'string'
    && typeof value.error.message === 'string' && typeof value.error.retryable === 'boolean');
const invoiceSummary: ResponseGuard = value => record(value) && processingSnapshot(value)
  && nullableString(value.invoice_number) && nullableString(value.supplier_name) && nullableString(value.invoice_date)
  && nullableDecimal(value.total_amount) && nullableString(value.currency);
const invoiceLine: ResponseGuard = value => record(value) && nullableString(value.description)
  && nullableDecimal(value.quantity) && nullableDecimal(value.unit_price) && nullableDecimal(value.line_total)
  && nullableString(value.po_line_reference) && nullableString(value.po_number) && nullableInteger(value.po_line_number)
  && typeof value.unit_price_basis === 'string' && ['net', 'gross', 'unknown'].includes(value.unit_price_basis);
const accountingEvidence: ResponseGuard = value => value === null || record(value)
  && (value.taxes_separately_stated === null || typeof value.taxes_separately_stated === 'boolean')
  && (value.adjustments === null || Array.isArray(value.adjustments) && value.adjustments.length <= 500
    && value.adjustments.every(item => record(item) && typeof item.kind === 'string'
      && ['discount', 'charge', 'rounding', 'unknown'].includes(item.kind)
      && typeof item.stage === 'string' && ['before_subtotal', 'after_subtotal', 'unknown'].includes(item.stage)
      && nullableDecimal(item.amount) && nullableInteger(item.line_index)));
const invoiceDraft: ResponseGuard = value => record(value) && nullableString(value.supplier_name)
  && nullableString(value.supplier_tax_id) && nullableString(value.invoice_number) && nullableString(value.invoice_date)
  && nullableString(value.due_date) && (value.po_references === null || strings(value.po_references))
  && nullableString(value.currency) && nullableDecimal(value.subtotal) && nullableDecimal(value.tax_amount)
  && nullableDecimal(value.total_amount) && Array.isArray(value.lines) && value.lines.every(invoiceLine)
  && (value.accounting_evidence === undefined || accountingEvidence(value.accounting_evidence));
const finding: ResponseGuard = value => record(value) && typeof value.code === 'string' && ruleOutcome(value.outcome)
  && typeof value.reason === 'string' && (value.po_number === undefined || nullableString(value.po_number))
  && (value.line_index === undefined || nullableInteger(value.line_index))
  && (value.po_line_number === undefined || nullableInteger(value.po_line_number));
const documentValidation: ResponseGuard = value => {
  if (!record(value) || value.policy_version !== 'document-type-v1' || !documentType(value.document_type)
    || value.document_type === 'invoice' || !Array.isArray(value.findings) || value.findings.length !== 1) return false;
  const uncertain = value.document_type === 'unknown';
  return value.automatic_outcome === (uncertain ? 'Held for Review' : 'Rejected')
    && value.operational_state === (uncertain ? 'REVIEW_REQUIRED' : 'REJECTED')
    && value.findings.every(item => record(item) && finding(item)
      && item.code === (uncertain ? 'DOCUMENT_TYPE_UNCERTAIN' : 'INVALID_DOCUMENT_TYPE')
      && item.outcome === (uncertain ? 'HOLD' : 'REJECT'));
};
const ruleValues: ResponseGuard = value => record(value) && decimal(value.price_tolerance) && decimal(value.analyst_approval_limit);
const evaluation: ResponseGuard = value => record(value) && integer(value.invoice_id) && integer(value.configuration_id)
  && typeof value.evaluated_at === 'string' && typeof value.actor_id === 'string' && typeof value.origin === 'string'
  && nullableInteger(value.evaluation_id) && record(value.result) && outcome(value.result.provisional_status)
  && nullableOutcome(value.result.final_status) && Array.isArray(value.result.rules) && value.result.rules.every(finding)
  && ruleValues(value.result.config) && record(value.input_snapshot) && record(value.context_snapshot);
const invoiceDetail: ResponseGuard = value => record(value) && processingSnapshot(value) && record(value.document)
  && typeof value.document.file_name === 'string' && typeof value.document.media_type === 'string'
  && (INVOICE_MEDIA_TYPES as readonly string[]).includes(value.document.media_type)
  && integer(value.document.byte_size) && typeof value.document.sha256 === 'string' && integer(value.document.page_count)
  && nullableInteger(value.document.pixel_width) && nullableInteger(value.document.pixel_height)
  && (value.extraction === null || record(value.extraction) && typeof value.extraction.is_invoice === 'boolean'
    && (value.extraction.invoice === null || invoiceDraft(value.extraction.invoice)) && strings(value.extraction.warnings)
    && (value.extraction.document_type === undefined || documentType(value.extraction.document_type)))
  && (value.evaluation === null || evaluation(value.evaluation)) && Array.isArray(value.findings) && value.findings.every(finding)
  && (value.document_validation === undefined || value.evaluation === null);
const processingDetail: ResponseGuard = value => record(value) && processingSnapshot(value)
  && Array.isArray(value.attempt_history) && value.attempt_history.every(attempt => record(attempt)
    && integer(attempt.number) && typeof attempt.started_at === 'string' && nullableString(attempt.finished_at)
    && integer(attempt.reserved_tokens) && nullableInteger(attempt.reported_tokens) && nullableString(attempt.error_code))
  && Array.isArray(value.events) && value.events.every(event => record(event) && typeof event.at === 'string'
    && processingState(event.state) && nullableString(event.error_code));
const priceBasisMetadata: ResponseGuard = value => record(value)
  && (['declared_unit_price_basis', 'effective_unit_price_basis', 'unit_price_basis_source', 'price_basis_reason']
    .every(key => value[key] === undefined)
    || ['declared_unit_price_basis', 'effective_unit_price_basis'].every(key => typeof value[key] === 'string'
      && ['net', 'gross', 'unknown'].includes(value[key] as string))
      && typeof value.unit_price_basis_source === 'string'
      && ['declared', 'arithmetic', 'undetermined'].includes(value.unit_price_basis_source)
      && typeof value.price_basis_reason === 'string');
const comparisonRow: ResponseGuard = value => record(value) && integer(value.line_index) && nullableString(value.description)
  && nullableString(value.po_number) && nullableInteger(value.po_line_number)
  && ['quantity_invoiced', 'quantity_ordered', 'quantity_received', 'quantity_previously_approved', 'quantity_available',
    'po_net_unit_price', 'invoice_net_unit_price', 'tolerance'].every(key => nullableDecimal(value[key]))
  && nullableString(value.invoice_currency) && nullableString(value.po_currency)
  && typeof value.association_status === 'string' && ['explicit', 'unresolved'].includes(value.association_status)
  && priceBasisMetadata(value)
  && Array.isArray(value.findings)
  && value.findings.every(item => record(item) && typeof item.rule === 'string' && typeof item.message === 'string'
    && record(item.evidence) && ruleOutcome(item.evidence.outcome) && nullableString(item.evidence.po_number)
    && nullableInteger(item.evidence.line_index) && nullableInteger(item.evidence.po_line_number));
const decision: ResponseGuard = value => record(value) && integer(value.id) && integer(value.revision)
  && integer(value.extraction_version) && nullableInteger(value.evaluation_id) && reviewAction(value.action)
  && typeof value.actor_id === 'string' && typeof value.at === 'string' && typeof value.comment === 'string';
const reviewDetail: ResponseGuard = value => record(value) && uuid(value.invoice_id) && integer(value.revision)
  && integer(value.extraction_version) && (value.extraction === null || invoiceDraft(value.extraction))
  && (value.original_extraction === null || invoiceDraft(value.original_extraction))
  && (value.evaluation === null || evaluation(value.evaluation)) && Array.isArray(value.comparison_rows)
  && value.comparison_rows.every(comparisonRow) && reviewState(value.operational_state)
  && (value.document_validation === undefined || documentValidation(value.document_validation)
    && value.evaluation === null && value.comparison_rows.length === 0 && value.payment_blocked === true)
  && typeof value.payment_blocked === 'boolean' && typeof value.credit_note_pending === 'boolean'
  && typeof value.requires_reevaluation === 'boolean' && record(value.eligibility)
  && typeof value.eligibility.allowed === 'boolean' && typeof value.eligibility.requires_manager === 'boolean'
  && nullableString(value.eligibility.reason) && decimal(value.eligibility.limit_amount) && value.eligibility.limit_currency === 'USD'
  && (value.current_decision === null || decision(value.current_decision)) && typeof value.document_url === 'string'
  && typeof value.created_at === 'string' && typeof value.updated_at === 'string';
const reviewSummary: ResponseGuard = value => record(value) && uuid(value.invoice_id) && integer(value.revision)
  && nullableString(value.invoice_number) && nullableString(value.supplier_name) && nullableString(value.invoice_date)
  && nullableDecimal(value.total_amount) && nullableString(value.currency) && reviewState(value.operational_state)
  && typeof value.payment_blocked === 'boolean' && nullableOutcome(value.automatic_outcome) && strings(value.reasons)
  && typeof value.requires_manager === 'boolean' && typeof value.created_at === 'string' && integer(value.age_seconds);
const reviewHistory: ResponseGuard = value => record(value) && uuid(value.invoice_id) && integer(value.revision)
  && Array.isArray(value.events) && value.events.every(event => record(event) && typeof event.kind === 'string'
    && typeof event.at === 'string' && typeof event.actor_id === 'string' && typeof event.comment === 'string'
    && typeof event.current === 'boolean' && (event.actor_name === undefined || typeof event.actor_name === 'string')
    && (event.actor_role === undefined || typeof event.actor_role === 'string')
    && ['revision', 'extraction_version', 'decision_id'].every(key => event[key] === undefined || integer(event[key]))
    && (event.evaluation_id === undefined || nullableInteger(event.evaluation_id))
    && (event.action === undefined || reviewAction(event.action))
    && (event.evaluation === undefined || event.evaluation === null || evaluation(event.evaluation))
    && (event.invoice === undefined || event.invoice === null || invoiceDraft(event.invoice)));
const parameterVersion: ResponseGuard = value => record(value) && ruleValues(value)
  && integer(value.version_id) && value.currency === 'USD' && typeof value.effective_from === 'string'
  && typeof value.actor_reference === 'string' && (value.actor === null || record(value.actor)
    && integer(value.actor.actor_id) && typeof value.actor.username === 'string' && typeof value.actor.role === 'string');
const page: ResponseGuard = value => record(value) && integer(value.total) && integer(value.offset)
  && integer(value.limit) && Number(value.limit) > 0;
const invoicePage: ResponseGuard = value => record(value) && page(value) && Array.isArray(value.items) && value.items.every(invoiceSummary);
const reviewPage: ResponseGuard = value => record(value) && page(value) && Array.isArray(value.items) && value.items.every(reviewSummary);
const parameterHistory: ResponseGuard = value => record(value) && page(value) && Array.isArray(value.items)
  && value.items.every(item => record(item) && parameterVersion(item) && nullableInteger(item.previous_version_id)
    && (item.before === null || ruleValues(item.before)) && ruleValues(item.after) && nullableString(item.comment)
    && ['manager_change', 'bootstrap', 'historical_unknown'].includes(String(item.provenance)));
const auditKinds = ['creation', 'extraction', 'evaluation', 'correction', 'reevaluation', 'decision',
  'escalation', 'credit_note_request', 'state_change', 'approval_reversal', 'document_validation'];
const auditEvent: ResponseGuard = value => record(value) && typeof value.id === 'string'
  && auditKinds.includes(String(value.kind)) && nullableString(value.at) && record(value.actor)
  && nullableString(value.actor.id) && nullableString(value.actor.name) && nullableString(value.actor.role)
  && ['recorded', 'system', 'unknown'].includes(String(value.actor.type)) && record(value.provenance)
  && typeof value.provenance.source === 'string' && typeof value.provenance.source_id === 'string'
  && typeof value.provenance.derived === 'boolean' && nullableString(value.provenance.note)
  && nullableInteger(value.revision) && nullableInteger(value.extraction_version) && nullableInteger(value.evaluation_id)
  && typeof value.current === 'boolean' && nullableString(value.comment) && record(value.evidence)
  && Array.isArray(value.diffs) && value.diffs.every(diff => record(diff) && typeof diff.field === 'string'
    && 'before' in diff && 'after' in diff && typeof diff.before_known === 'boolean'
    && typeof diff.derived === 'boolean' && typeof diff.source === 'string');
const auditHistory: ResponseGuard = value => record(value) && page(value) && typeof value.invoice_id === 'string'
  && integer(value.revision) && Array.isArray(value.events) && value.events.every(auditEvent);

function decode<T>(status: number, text: string, correlationId: string | null, validate?: ResponseGuard): T {
  const data = parse(text);
  if (status < 200 || status >= 300) throw httpError(status, data, correlationId);
  if (status === 204 && !validate) return undefined as T;
  if (!record(data) || validate && !validate(data)) {
    throw new ApiError(status, 'invalid_response', 'Unexpected service response', true, correlationId);
  }
  // Check wire shape only. Matching, authorization and parameter policy remain server-side.
  return data as T;
}
async function request<T>(path: string, init: RequestInit = {}, validate?: ResponseGuard): Promise<T> {
  try {
    const response = await fetch(ROOT + path, { ...init, credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
    return decode<T>(response.status, await response.text(), response.headers.get('x-correlation-id'), validate);
  } catch (error) {
    if (error instanceof ApiError || (error instanceof Error && error.name === 'AbortError')) throw error;
    throw new ApiError(0, 'network_error', 'Network connection interrupted', true);
  }
}
function invoicePath(id: string): string {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    throw new TypeError('Invalid invoice ID');
  }
  return `/invoices/${id}`;
}
function reviewCasePath(id: string): string {
  return invoicePath(id).replace('/invoices/', '/review/');
}
function reviewMutation(path: string, method: 'POST' | 'PATCH', revision: number, csrfToken: string, body: unknown) {
  if (!Number.isInteger(revision) || revision < 0 || revision > 2147483647) throw new TypeError('Invalid case revision');
  return request<ReviewDetail>(path, { method, headers: {
    'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken, 'If-Match': `"${revision}"`,
  }, body: JSON.stringify(body) }, reviewDetail);
}
function queryString(options: object): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) if (value !== undefined && value !== '') query.set(key, String(value));
  return query.size ? `?${query}` : '';
}
export interface UploadProgress { loaded: number; total: number | null }
export interface UploadOptions {
  csrfToken: string;
  /** Retain this exact key for uncertain retries of the same file. The client never generates it. */
  idempotencyKey: string;
  onProgress?: (progress: UploadProgress) => void;
}

function uploadInvoice(file: File, options: UploadOptions): Promise<ProcessingSnapshot> {
  return new Promise((resolve, reject) => {
    if (!options.idempotencyKey.trim() || options.idempotencyKey.length > 200) {
      reject(new ApiError(422, 'invalid_idempotency_key', 'A stable Idempotency-Key is required', false));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', ROOT + '/invoices');
    xhr.withCredentials = true;
    xhr.timeout = 30_000;
    xhr.setRequestHeader('X-CSRF-Token', options.csrfToken);
    xhr.setRequestHeader('Idempotency-Key', options.idempotencyKey);
    xhr.upload.onprogress = event => options.onProgress?.({
      loaded: event.loaded, total: event.lengthComputable ? event.total : null,
    });
    xhr.onload = () => {
      try { resolve(decode<ProcessingSnapshot>(xhr.status, xhr.responseText, xhr.getResponseHeader('X-Correlation-ID'), processingSnapshot)); }
      catch (error) { reject(error); }
    };
    xhr.onerror = () => reject(new ApiError(0, 'network_error', 'Network connection interrupted', true));
    xhr.ontimeout = () => reject(new ApiError(0, 'timeout', 'Upload timed out; retry with the same key', true));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    const form = new FormData();
    form.append('file', file);
    // Browser chooses multipart boundary; do not set Content-Type manually.
    xhr.send(form);
  });
}

/** Same-origin transport only; auth/CSRF/ownership remain authoritative in FastAPI. */
export const api = {
  currentSession: (signal?: AbortSignal) => request<Session>('/sessions/current', { signal }, session),
  login: (username: string, password: string) => request<Session>('/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  }, session),
  logout: (csrfToken: string) => request<void>('/sessions/current', {
    method: 'DELETE', headers: { 'X-CSRF-Token': csrfToken },
  }),
  list: (options: ListOptions = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    if (options.offset !== undefined) query.set('offset', String(options.offset));
    if (options.limit !== undefined) query.set('limit', String(options.limit));
    if (options.status !== undefined) query.set('status', options.status);
    return request<InvoicePage>('/invoices' + (query.size ? `?${query}` : ''), { signal }, invoicePage);
  },
  detail: (id: string, signal?: AbortSignal) => request<InvoiceDetail>(invoicePath(id), { signal }, invoiceDetail),
  processing: (id: string, signal?: AbortSignal) => request<ProcessingDetail>(invoicePath(id) + '/processing', { signal }, processingDetail),
  retry: (id: string, csrfToken: string) => request<ProcessingSnapshot>(invoicePath(id) + '/retry', {
    method: 'POST', headers: { 'X-CSRF-Token': csrfToken },
  }, processingSnapshot),
  uploadInvoice,
  documentURL: (id: string) => ROOT + invoicePath(id) + '/document',
  reviewList: (options: ReviewListOptions = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(options)) if (value !== undefined && value !== '') query.set(key, String(value));
    return request<ReviewQueuePage>('/review' + (query.size ? `?${query}` : ''), { signal }, reviewPage);
  },
  reviewDetail: (id: string, signal?: AbortSignal) => request<ReviewDetail>(reviewCasePath(id), { signal }, reviewDetail),
  reviewHistory: (id: string, signal?: AbortSignal) => request<ReviewHistory>(reviewCasePath(id) + '/history', { signal }, reviewHistory),
  correctExtraction: (id: string, revision: number, invoice: InvoiceDraft, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id), 'PATCH', revision, csrf, { invoice, comment }),
  reevaluate: (id: string, revision: number, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id) + '/reevaluations', 'POST', revision, csrf, { comment }),
  decide: (id: string, revision: number, action: ReviewAction, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id) + '/decisions', 'POST', revision, csrf, { action, comment }),
  escalate: (id: string, revision: number, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id) + '/escalations', 'POST', revision, csrf, { comment }),
  parameters: (signal?: AbortSignal) => request<ParameterVersion>('/manager/parameters', { signal }, parameterVersion),
  parameterHistory: (options: { offset?: number; limit?: number } = {}, signal?: AbortSignal) =>
    request<ParameterHistory>('/manager/parameters/history' + queryString(options), { signal }, parameterHistory),
  updateParameters: (version: number, values: ParameterUpdate, csrf: string) => {
    if (!Number.isInteger(version) || version < 1 || version > 2147483647) throw new TypeError('Invalid parameter version');
    return request<ParameterVersion>('/manager/parameters', { method: 'PATCH', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': csrf, 'If-Match': `"${version}"`,
    }, body: JSON.stringify(values) }, parameterVersion);
  },
  auditHistory: (id: string, options: AuditFilters = {}, signal?: AbortSignal) =>
    request<AuditHistory>(invoicePath(id).replace('/invoices/', '/audit/') + queryString(options), { signal }, auditHistory),
};
