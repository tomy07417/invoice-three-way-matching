import type { AutomaticOutcome, ProcessingState } from './contracts';

export const PROCESSING_STATES: ProcessingState[] = [
  'QUEUED', 'EXTRACTING', 'EXTRACTED', 'EVALUATING', 'RETRY_WAIT', 'COMPLETED',
  'EXTRACTION_FAILED', 'EVALUATION_INCOMPLETE', 'PROCESSING_FAILED',
];
export function isActive(status: ProcessingState): boolean {
  return ['QUEUED', 'EXTRACTING', 'EXTRACTED', 'EVALUATING', 'RETRY_WAIT'].includes(status);
}
export function safeReturnPath(value: string | null): string {
  const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  const routes = new RegExp(`^(?:/invoices(?:/new|/${uuid})?|/(?:review|audit)(?:/${uuid})?|/manager(?:/parameters|/escalations)?)$`, 'i');
  return value && routes.test(value) ? value : '/invoices';
}
export function validateFile(file: { type: string; size: number }, maximum: number, types: readonly string[]): string | null {
  if (!types.includes(file.type)) return 'Choose a PDF, PNG or JPG (JPEG) document.';
  if (file.size === 0) return 'The selected document is empty.';
  if (file.size > maximum) return 'The document must be at most 8 MiB.';
  return null;
}
export function displayValue(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === '' ? '—' : String(value);
}
export function outcomeLabel(status: ProcessingState, outcome: AutomaticOutcome | null): string {
  if (status === 'EVALUATION_INCOMPLETE') return 'Unknown — evaluation incomplete';
  if (status === 'EXTRACTION_FAILED' || status === 'PROCESSING_FAILED') return '—';
  return outcome ?? '—';
}
export function pollDecision(errors: number, elapsed: number, active: boolean): {
  delay: number | null; reason: 'terminal' | 'errors' | 'timeout' | null;
} {
  if (!active) return { delay: null, reason: 'terminal' };
  if (errors >= 3) return { delay: null, reason: 'errors' };
  if (elapsed >= 120000) return { delay: null, reason: 'timeout' };
  return { delay: Math.min(3000 * 2 ** errors, 30000), reason: null };
}
export function isCancelled(error: unknown): boolean {
  return !!error && typeof error === 'object' && 'name' in error && error.name === 'AbortError';
}
export function errorMessage(error: unknown): string {
  const status = error && typeof error === 'object' && 'status' in error ? error.status : 0;
  const messages: Record<number, string> = {
    0: 'Connection interrupted. Refresh to recover; retry an upload with the same file to keep its submission key.',
    401: 'Your session has expired. Sign in again to continue.',
    403: 'Access denied. Return to your invoice list or sign in with an authorized account.',
    404: 'Invoice not found or unavailable to this account. Return to the invoice list.',
    413: 'The document exceeds the server size or page/pixel limit. Choose a smaller document.',
    415: 'Unsupported document. Choose a PDF, PNG or JPG (JPEG).',
    422: 'The server could not accept this input. Check the document or fields and try again.',
    429: 'Too many requests. Wait before trying again; your existing submission key is retained.',
  };
  return messages[status as number] ?? 'The service is temporarily unavailable. Refresh to recover; upload retries retain the same submission key.';
}
