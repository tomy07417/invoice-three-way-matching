import type { ReactNode } from 'react';
import { SessionGate } from '../../components/intake/session-gate';
import { ManagerGate } from '../../components/access/manager-gate';
import './audit.css';

export default function AuditLayout({ children }: { children: ReactNode }) {
  return <SessionGate><ManagerGate>{children}</ManagerGate></SessionGate>;
}
