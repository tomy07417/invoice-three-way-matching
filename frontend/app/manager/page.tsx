import Link from 'next/link';

export default function ManagerPage() {
  return <main><h1>AP Manager workspace</h1><p>Manage future rule parameters, resolve escalations and inspect recorded evidence.</p>
    <ul className="manager-links"><li><Link href="/manager/parameters">Rule parameters and change history</Link><p>Versioned changes with confirmation and an audit trail.</p></li>
      <li><Link href="/manager/escalations">Pending escalations</Link><p>Review Analyst comments and resolve cases through the existing AP decision workflow.</p></li>
      <li><Link href="/audit">Complete invoice audit</Link><p>Inspect historical decisions, corrections and evaluations.</p></li></ul>
    <p>Manager capabilities include <Link href="/review">AP review</Link>. The API independently verifies your persisted role for each operation.</p>
  </main>;
}
