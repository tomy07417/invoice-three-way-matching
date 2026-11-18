import type { ReactNode } from 'react';
import Link from 'next/link';
import { SessionGate } from '../../components/intake/session-gate';
import { ManagerGate } from '../../components/access/manager-gate';
import './manager.css';

export default function ManagerLayout({ children }: { children: ReactNode }) {
  return <SessionGate><ManagerGate><div className="manager-workspace">
    <nav aria-label="Manager navigation"><Link href="/manager">Manager home</Link><Link href="/manager/parameters">Rule parameters</Link>
      <Link href="/manager/escalations">Escalations</Link><Link href="/audit">Complete audit</Link></nav>
    {children}
  </div></ManagerGate></SessionGate>;
}
