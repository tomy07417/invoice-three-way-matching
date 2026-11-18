'use client';

import { useCallback, useEffect, useState } from 'react';
import { errorMessage, isCancelled, pollDecision } from '../../lib/intake-state';
import { useSession } from './session-gate';

/** One read at a time, cancellable on navigation; refresh explicitly starts a new bounded window. */
export function usePolling<T>(load: (signal: AbortSignal) => Promise<T>, active: (data: T) => boolean) {
  const { handleError } = useSession();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [paused, setPaused] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let errors = 0;
    let latest: T | null = null;
    const started = Date.now();
    setData(null);
    setError(null);
    setPaused(null);
    setLoading(true);
    async function read() {
      // A hidden tab cannot generate a background request storm.
      if (document.hidden) { schedule(true); return; }
      try {
        const next = await load(controller.signal);
        if (controller.signal.aborted) return;
        latest = next;
        errors = 0;
        setData(next);
        setError(null);
      } catch (failure) {
        if (controller.signal.aborted || isCancelled(failure)) return;
        handleError(failure);
        errors += 1;
        setError(errorMessage(failure));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
      if (!controller.signal.aborted) schedule(latest === null || active(latest));
    }
    function schedule(stillActive: boolean) {
      const decision = pollDecision(errors, Date.now() - started, stillActive);
      if (decision.reason !== null) setLoading(false);
      if (decision.reason === 'errors') setPaused('Automatic refresh paused after three read errors. Refresh to resume.');
      if (decision.reason === 'timeout') setPaused('Background processing continues; refresh to resume.');
      if (decision.delay !== null) timer = setTimeout(read, decision.delay);
    }
    void read();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [load, active, handleError, version]);
  return { data, error, loading, paused, refresh };
}
