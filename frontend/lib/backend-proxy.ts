// Server route helper only. Never import this module into browser components.
export const MAX_PROXY_BODY_BYTES = 8 * 1024 * 1024 + 64 * 1024;
const TIMEOUT_MS = 15_000;
const UUID = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
const invoicePath = new RegExp(`^/invoices/${UUID}(/processing|/document|/retry)?$`);
const reviewPath = new RegExp(`^/review/${UUID}(/history|/reevaluations|/decisions|/escalations)?$`);
const auditPath = new RegExp(`^/audit/${UUID}$`);
const auditQuery = ['offset', 'limit', 'kind', 'actor_id', 'time_from', 'time_to'];
const reviewQuery = ['offset', 'limit', 'supplier', 'date_from', 'date_to', 'min_amount', 'max_amount',
  'reason', 'operational_state', 'min_age_days', 'sort', 'direction', 'outcome'];

function privateHeaders(source?: Headers): Headers {
  const headers = new Headers();
  for (const name of ['content-type', 'content-disposition', 'x-content-type-options',
    'x-correlation-id', 'retry-after', 'content-security-policy', 'x-frame-options']) {
    const value = source?.get(name);
    if (value) headers.set(name, value);
  }
  // getSetCookie keeps expiry commas and multiple independent cookie lines intact.
  for (const cookie of source?.getSetCookie() ?? []) headers.append('set-cookie', cookie);
  headers.set('cache-control', 'private, no-store');
  headers.set('vary', source?.get('vary') ? `${source.get('vary')}, Cookie` : 'Cookie');
  headers.set('x-content-type-options', 'nosniff');
  return headers;
}

function failure(status: number, code: string, message: string, source?: Headers): Response {
  const headers = privateHeaders(source);
  headers.set('content-type', 'application/json');
  headers.delete('content-disposition');
  return Response.json({ error: { code, message, retryable: status === 429 || status >= 500,
    correlation_id: source?.get('x-correlation-id') ?? null } }, { status, headers });
}

function backendOrigin(): string {
  const url = new URL(process.env.BACKEND_INTERNAL_URL ?? 'http://127.0.0.1:8000');
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
    || url.pathname !== '/' || url.search || url.hash) throw new Error('Invalid backend origin');
  return url.origin;
}

function frontendOrigin(requestOrigin: string): string {
  const configured = process.env.FRONTEND_ORIGIN;
  if (configured === undefined) return requestOrigin;
  // Trusted deployment configuration, never Host/X-Forwarded-Host or the caller's Origin.
  // Check the spelling before URL normalization can erase paths or empty query/fragment markers.
  if (!/^https?:\/\/[^/?#\\@\s]+\/?$/.test(configured)) throw new Error('Invalid frontend origin');
  return new URL(configured).origin;
}

// Buffer only the bounded admission body, never an unbounded request.arrayBuffer().
async function readBody(request: Request, signal: AbortSignal): Promise<Uint8Array | undefined> {
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', cancel, { once: true });
  try {
    signal.throwIfAborted();
    while (true) {
      const { done, value } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_PROXY_BODY_BYTES) {
        cancel();
        throw new RangeError('Body limit');
      }
      chunks.push(value);
    }
    const body = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    return body;
  } finally {
    signal.removeEventListener('abort', cancel);
    reader.releaseLock();
  }
}

export async function proxyBackend(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const prefix = '/api/backend';
  const path = url.pathname.startsWith(prefix + '/') ? url.pathname.slice(prefix.length) : '';
  // No decoding: encoded separators, traversal and alternative route spellings are not admitted.
  let methods: string[];
  if (path === '/sessions') methods = ['POST'];
  else if (path === '/sessions/current') methods = ['GET', 'DELETE'];
  else if (path === '/invoices') methods = ['GET', 'POST'];
  else if (invoicePath.test(path)) methods = [path.endsWith('/retry') ? 'POST' : 'GET'];
  else if (path === '/manager/parameters') methods = ['GET', 'PATCH'];
  else if (path === '/manager/parameters/history' || auditPath.test(path)) methods = ['GET'];
  else if (path === '/review') methods = ['GET'];
  else if (reviewPath.test(path)) methods = path.endsWith('/history') ? ['GET']
    : /\/(reevaluations|decisions|escalations)$/.test(path) ? ['POST'] : ['GET', 'PATCH'];
  else return failure(404, 'not_found', 'Route not found');
  if (!methods.includes(request.method)) return failure(405, 'method_not_allowed', 'Method not allowed');
  let expectedOrigin: string;
  try { expectedOrigin = frontendOrigin(url.origin); }
  catch { return failure(503, 'proxy_configuration', 'Service temporarily unavailable'); }
  const mutation = request.method !== 'GET';
  if (mutation && request.headers.get('origin') !== expectedOrigin) {
    return failure(403, 'invalid_origin', 'Same-origin request required');
  }
  const queryKeys = request.method !== 'GET' ? [] : path === '/invoices' ? ['offset', 'limit', 'status']
    : path === '/review' ? reviewQuery : path === '/manager/parameters/history' ? ['offset', 'limit']
      : auditPath.test(path) ? auditQuery : [];
  for (const key of url.searchParams.keys()) {
    if (!queryKeys.includes(key) || url.searchParams.getAll(key).length !== 1) {
      return failure(400, 'invalid_query', 'Invalid query parameters');
    }
  }
  const length = request.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)))) {
    return failure(400, 'invalid_length', 'Invalid Content-Length');
  }
  if (length !== null && Number(length) > MAX_PROXY_BODY_BYTES) {
    return failure(413, 'body_too_large', 'Invoice is too large');
  }
  let origin: string;
  try { origin = backendOrigin(); }
  catch { return failure(503, 'proxy_configuration', 'Service temporarily unavailable'); }
  const headers = new Headers();
  const cookie = request.headers.get('cookie')?.split(';').map(value => value.trim())
    .find(value => value.startsWith('demo_session='));
  if (cookie) headers.set('cookie', cookie);
  for (const name of ['x-csrf-token', 'idempotency-key', 'content-type']) {
    const value = request.headers.get(name);
    if (mutation && value) headers.set(name, value);
  }
  const versionedPath = path.startsWith('/review/') || path === '/manager/parameters';
  if (versionedPath && mutation && request.headers.has('if-match')) {
    headers.set('if-match', request.headers.get('if-match')!);
  }
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(TIMEOUT_MS)]);
  try {
    const body = request.method === 'POST' || request.method === 'PATCH' ? await readBody(request, signal) : undefined;
    const upstream = await fetch(origin + path + url.search, {
      method: request.method, headers, body: body as BodyInit | undefined,
      cache: 'no-store', redirect: 'manual', signal,
    });
    if (upstream.status >= 300 && upstream.status < 400) {
      void upstream.body?.cancel();
      return failure(502, 'upstream_redirect', 'Unexpected service response');
    }
    const responseHeaders = privateHeaders(upstream.headers);
    // Native document frames must never render a successful HTML/SVG or unknown response.
    if (path.endsWith('/document') && upstream.ok) {
      const mediaType = upstream.headers.get('content-type')?.split(';')[0].trim().toLowerCase();
      if (!['application/pdf', 'image/png', 'image/jpeg'].includes(mediaType ?? '')) {
        void upstream.body?.cancel();
        return failure(502, 'unsupported_document_type', 'Original document could not be displayed safely', upstream.headers);
      }
    }
    if ((versionedPath || auditPath.test(path)) && upstream.headers.has('etag')) {
      responseHeaders.set('etag', upstream.headers.get('etag')!);
    }
    if (upstream.status === 204) return new Response(null, { status: 204, headers: responseHeaders });
    const content = await upstream.arrayBuffer();
    if (!upstream.ok && !upstream.headers.get('content-type')?.includes('application/json')) {
      return failure(upstream.status, `http_${upstream.status}`, 'Request could not be completed', upstream.headers);
    }
    return new Response(content, { status: upstream.status, headers: responseHeaders });
  } catch (error) {
    if (error instanceof RangeError) return failure(413, 'body_too_large', 'Invoice is too large');
    if (signal.aborted || (error instanceof Error && error.name === 'TimeoutError')) {
      return failure(504, 'timeout', 'Request timed out');
    }
    return failure(502, 'network_error', 'Service could not be reached');
  }
}
