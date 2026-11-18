import { proxyBackend } from '../../../../lib/backend-proxy';

export const dynamic = 'force-dynamic';
export const GET = proxyBackend;
export const POST = proxyBackend;
export const DELETE = proxyBackend;
// Keep unsupported-method responses private too, rather than Next's implicit handlers.
export const HEAD = proxyBackend;
export const OPTIONS = proxyBackend;
export const PUT = proxyBackend;
export const PATCH = proxyBackend;
