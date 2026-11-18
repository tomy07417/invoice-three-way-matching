'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { useSession } from '../intake/session-gate';

/** Presentation guard only: each Manager API independently enforces persisted roles. */
export function ManagerGate({ children }: { children: ReactNode }) {
  const { session } = useSession();
  if (session.actor.role !== 'ap_manager') return <main><h1>Manager access required</h1>
    <p role="alert">Only an AP Manager can access administration and complete audit.</p>
    <Link href="/invoices">Return to invoices</Link></main>;
  return children;
}
