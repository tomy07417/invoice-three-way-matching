import type { ReactNode } from 'react';
import { SessionGate } from '../../components/intake/session-gate';
import { ReviewGate } from '../../components/review/review-gate';
import './review.css';

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return <SessionGate><ReviewGate>{children}</ReviewGate></SessionGate>;
}
