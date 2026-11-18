'use client';

import { useEffect, useState } from 'react';
import { formatMoney, formatTimestamp } from '../../lib/display-format';

function useDisplayPreferences(locale?: string, timeZone?: string) {
  const [preferences, setPreferences] = useState<{ locale: string; timeZone: string } | null>(null);
  useEffect(() => {
    setPreferences({ locale: locale ?? navigator.language ?? 'en-US',
      timeZone: timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone });
  }, [locale, timeZone]);
  return preferences;
}

/** Server and first client render are identical; localization begins only after hydration. */
export function MoneyValue({ amount, currency, locale }: {
  amount: string | null | undefined; currency: string | null | undefined; locale?: string;
}) {
  const preferences = useDisplayPreferences(locale);
  const original = `${amount ?? 'Amount unknown'} · ${currency ?? 'Currency unknown'}`;
  const display = preferences ? formatMoney(amount, currency, preferences.locale) : original;
  return <span title={`Recorded value: ${original}`} aria-label={`${display}; recorded value: ${original}`}>
    {display}
  </span>;
}

export function TimestampValue({ value, locale, timeZone }: {
  value: string | null | undefined; locale?: string; timeZone?: string;
}) {
  const preferences = useDisplayPreferences(locale, timeZone);
  const original = value ?? 'Time unknown';
  const display = preferences ? formatTimestamp(value, preferences.locale, preferences.timeZone) : original;
  // Do not emit an invalid dateTime for historical unknown/invalid metadata.
  const known = value && !formatTimestamp(value, 'en-US', 'UTC').startsWith('Time unknown');
  return <time dateTime={known ? value : undefined} title={`Recorded time: ${original}`}
    aria-label={`${display}; recorded time: ${original}`}>{display}</time>;
}
