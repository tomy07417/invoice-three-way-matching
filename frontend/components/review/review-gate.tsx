'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { useSession } from '../intake/session-gate';
import { isAP } from '../../lib/review-state';

export function ReviewGate({ children }: { children: ReactNode }) {
  const { session } = useSession();
  if (!isAP(session.actor.role)) return <main><h1>AP access required</h1>
    <p role="alert">Only an AP Analyst or AP Manager can access this workspace.</p>
    <Link href="/invoices">Return to invoice intake</Link></main>;
  return children;
}
