'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, ApiError } from '../../lib/api';
import type { Session } from '../../lib/contracts';
import { errorMessage, isCancelled, safeReturnPath } from '../../lib/intake-state';

interface SessionContextValue { session: Session; handleError: (error: unknown) => void }
const SessionContext = createContext<SessionContextValue | null>(null);
export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error('SessionGate is required');
  return value;
}
export function SessionGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [leaving, setLeaving] = useState(false);
  const handleError = useCallback((failure: unknown) => {
    if (failure instanceof ApiError && failure.status === 401) {
      setSession(null);
      router.replace(`/login?returnTo=${encodeURIComponent(safeReturnPath(pathname))}`);
    }
  }, [pathname, router]);
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.currentSession(controller.signal).then(setSession).catch(failure => {
      if (isCancelled(failure)) return;
      handleError(failure);
      setError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [version, handleError]);
  async function logout() {
    if (!session || leaving) return;
    setLeaving(true);
    try { await api.logout(session.csrf_token); router.replace('/login'); }
    catch (failure) { handleError(failure); setError(errorMessage(failure)); }
    finally { setLeaving(false); }
  }
  return <>
    <header className="site-header"><Link href="/invoices" className="brand">Invoice intake</Link>
      <nav aria-label="Main navigation"><Link href="/invoices">Invoices</Link><Link href="/invoices/new">New invoice</Link>
        {session ? <button onClick={logout} disabled={leaving}>Log out</button> : <Link href="/login">Log in</Link>}
      </nav>
    </header>
    {error && <div className="notice" role="alert">{error} <button onClick={() => setVersion(v => v + 1)}>Retry session</button></div>}
    {session ? <SessionContext.Provider value={{ session, handleError }}>{children}</SessionContext.Provider>
      : <main><p role="status">Checking session…</p><Link href="/login">Go to login</Link></main>}
  </>;
}
