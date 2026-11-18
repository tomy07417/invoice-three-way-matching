import type { InvoiceDetail, InvoiceDraft, InvoicePage, ListOptions, ProcessingDetail, ProcessingSnapshot, Session,
  ReviewAction, ReviewDetail, ReviewHistory, ReviewListOptions, ReviewQueuePage } from './contracts';

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
  const message = record(data) && typeof data.detail === 'string' ? data.detail
    : messages[status] ?? (status >= 500 ? 'Service temporarily unavailable' : 'Request failed');
  return new ApiError(status, `http_${status}`, message, status === 429 || status >= 500, correlationId);
}
function decode<T>(status: number, text: string, correlationId: string | null): T {
  const data = parse(text);
  if (status < 200 || status >= 300) throw httpError(status, data, correlationId);
  if (status === 204) return undefined as T;
  if (!record(data)) throw new ApiError(status, 'invalid_response', 'Unexpected service response', true, correlationId);
  // Contracts belong to FastAPI. Do not reimplement its business validation in the browser.
  return data as T;
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  try {
    const response = await fetch(ROOT + path, { ...init, credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
    return decode<T>(response.status, await response.text(), response.headers.get('x-correlation-id'));
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
  }, body: JSON.stringify(body) });
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
      try { resolve(decode<ProcessingSnapshot>(xhr.status, xhr.responseText, xhr.getResponseHeader('X-Correlation-ID'))); }
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
  currentSession: (signal?: AbortSignal) => request<Session>('/sessions/current', { signal }),
  login: (username: string, password: string) => request<Session>('/sessions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  }),
  logout: (csrfToken: string) => request<void>('/sessions/current', {
    method: 'DELETE', headers: { 'X-CSRF-Token': csrfToken },
  }),
  list: (options: ListOptions = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    if (options.offset !== undefined) query.set('offset', String(options.offset));
    if (options.limit !== undefined) query.set('limit', String(options.limit));
    if (options.status !== undefined) query.set('status', options.status);
    return request<InvoicePage>('/invoices' + (query.size ? `?${query}` : ''), { signal });
  },
  detail: (id: string, signal?: AbortSignal) => request<InvoiceDetail>(invoicePath(id), { signal }),
  processing: (id: string, signal?: AbortSignal) => request<ProcessingDetail>(invoicePath(id) + '/processing', { signal }),
  retry: (id: string, csrfToken: string) => request<ProcessingSnapshot>(invoicePath(id) + '/retry', {
    method: 'POST', headers: { 'X-CSRF-Token': csrfToken },
  }),
  uploadInvoice,
  documentURL: (id: string) => ROOT + invoicePath(id) + '/document',
  reviewList: (options: ReviewListOptions = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(options)) if (value !== undefined && value !== '') query.set(key, String(value));
    return request<ReviewQueuePage>('/review' + (query.size ? `?${query}` : ''), { signal });
  },
  reviewDetail: (id: string, signal?: AbortSignal) => request<ReviewDetail>(reviewCasePath(id), { signal }),
  reviewHistory: (id: string, signal?: AbortSignal) => request<ReviewHistory>(reviewCasePath(id) + '/history', { signal }),
  correctExtraction: (id: string, revision: number, invoice: InvoiceDraft, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id), 'PATCH', revision, csrf, { invoice, comment }),
  reevaluate: (id: string, revision: number, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id) + '/reevaluations', 'POST', revision, csrf, { comment }),
  decide: (id: string, revision: number, action: ReviewAction, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id) + '/decisions', 'POST', revision, csrf, { action, comment }),
  escalate: (id: string, revision: number, comment: string, csrf: string) =>
    reviewMutation(reviewCasePath(id) + '/escalations', 'POST', revision, csrf, { comment }),
};
