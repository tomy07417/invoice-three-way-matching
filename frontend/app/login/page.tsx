'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState, type FormEvent } from 'react';
import { api } from '../../lib/api';
import { errorMessage, safeReturnPath } from '../../lib/intake-state';

export default function LoginPage() {
  const router = useRouter();
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) return;
    const values = new FormData(event.currentTarget);
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await api.login(String(values.get('username') ?? ''), String(values.get('password') ?? ''));
      router.replace(safeReturnPath(new URLSearchParams(window.location.search).get('returnTo')));
    } catch (failure) { setError(errorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <main className="login"><h1>Log in</h1><p>Sign in with your assigned demo account to access invoice intake.</p>
    <form onSubmit={submit} aria-busy={busy}>
      <label htmlFor="username">Username</label><input id="username" name="username" autoComplete="username" maxLength={64} required />
      <label htmlFor="password">Password</label><input id="password" name="password" type="password" autoComplete="current-password" maxLength={1024} required />
      <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Log in'}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    <Link href="/invoices">Return to invoices</Link>
  </main>;
}
