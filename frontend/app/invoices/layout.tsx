import type { ReactNode } from 'react';
import { SessionGate } from '../../components/intake/session-gate';

export default function InvoiceLayout({ children }: { children: ReactNode }) {
  return <SessionGate>{children}</SessionGate>;
}
