'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, INVOICE_MEDIA_TYPES, MAX_INVOICE_BYTES, type UploadProgress } from '../../../lib/api';
import type { ProcessingSnapshot } from '../../../lib/contracts';
import { errorMessage, isCancelled, validateFile } from '../../../lib/intake-state';
import { useSession } from '../../../components/intake/session-gate';
import { InvoiceStatus } from '../../../components/intake/status';

export default function NewInvoicePage() {
  const { session, handleError } = useSession();
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const selection = useRef<{ file: File; key: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProcessingSnapshot | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  function choose(next: File | undefined) {
    if (inFlight.current || !next) return;
    const invalid = validateFile(next, MAX_INVOICE_BYTES, INVOICE_MEDIA_TYPES);
    setError(invalid);
    if (invalid) { setFile(null); return; }
    const previous = selection.current?.file;
    // Re-selecting the same document after an uncertain response must not create a second submission.
    if (!previous || previous.name !== next.name || previous.size !== next.size || previous.type !== next.type || previous.lastModified !== next.lastModified) {
      selection.current = { file: next, key: crypto.randomUUID() };
    }
    setFile(next);
    setProgress(null);
  }
  async function upload(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current || !file || !selection.current || result) return;
    const current = selection.current;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setProgress(null);
    try {
      const response = await api.uploadInvoice(current.file, {
        csrfToken: session.csrf_token, idempotencyKey: current.key,
        onProgress: value => { if (mounted.current) setProgress(value); },
      });
      if (mounted.current) setResult(response);
    } catch (failure) {
      if (!mounted.current || isCancelled(failure)) return;
      handleError(failure);
      setError(errorMessage(failure));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function newSubmission() {
    selection.current = null;
    setFile(null);
    setResult(null);
    setError(null);
    setProgress(null);
    if (input.current) { input.current.value = ''; input.current.focus(); }
  }
  return <main><h1>New invoice</h1>
    <p>Upload one PDF, PNG or JPG (JPEG), up to 8 MiB. The server also validates content, page count and pixel limits.</p>
    <form onSubmit={upload} aria-busy={busy}>
      <div className="drop-zone" onDragOver={event => event.preventDefault()} onDrop={event => {
        event.preventDefault();
        if (!result && !inFlight.current) {
          if (event.dataTransfer.files.length !== 1) setError('Choose one invoice document at a time.');
          else choose(event.dataTransfer.files[0]);
        }
      }}>
        <label htmlFor="invoice-file">Invoice document</label>
        <input ref={input} id="invoice-file" type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
          disabled={busy || !!result} onChange={event => choose(event.target.files?.[0])} aria-describedby="file-help" />
        <p id="file-help">Choose a file above or drop one here.</p>
      </div>
      {file && <p>Selected: {file.name} ({file.size} bytes)</p>}
      <button type="submit" disabled={!file || busy || !!result}>{busy ? 'Uploading…' : 'Upload invoice'}</button>
      {busy && <div role="status"><label htmlFor="upload-progress">Upload progress</label>
        <progress id="upload-progress" max={progress?.total ?? 1} value={progress?.total ? progress.loaded : undefined} />
        <p>{progress ? `${progress.loaded} bytes sent${progress.total ? ` of ${progress.total}` : ''}` : 'Preparing upload…'} — waiting for server confirmation.</p>
      </div>}
    </form>
    {error && <p role="alert">{error} No automatic upload retry was made. Retrying this selected file keeps its submission key.</p>}
    {result && <section aria-label="Upload success" role="status"><h2>Invoice received</h2>
      <p>Invoice ID: <Link href={`/invoices/${result.invoice_id}`}>{result.invoice_id}</Link></p>
      <InvoiceStatus invoice={result} /><button onClick={newSubmission}>Start a new submission</button>
    </section>}
    <p><Link href="/invoices">Return to invoices</Link></p>
  </main>;
}
