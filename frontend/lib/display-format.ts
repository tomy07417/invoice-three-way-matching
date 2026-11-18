/** Presentation only: never feed formatted text back into a mutation or matching rule. */
const DECIMAL = /^([+-]?)(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?$/;

/** Technical render budget, not an amount limit. Larger expansions retain exact source notation. */
export const MAX_EXPANDED_DIGITS = 1000;

function currencyLabel(currency: string | null | undefined): string {
  return currency && /^[A-Z]{3}$/.test(currency) ? currency
    : `Currency unknown${currency ? ` (${currency})` : ''}`;
}

/** No Number/parseFloat conversion of the amount, no currency default, no FX or rounding. */
export function formatMoney(amount: string | null | undefined, currency: string | null | undefined,
  locale: string): string {
  const label = currencyLabel(currency);
  const match = typeof amount === 'string' ? DECIMAL.exec(amount) : null;
  if (!match) return `Amount unknown${amount ? ` (${amount})` : ''} · ${label}`;
  if (!currency || !/^[A-Z]{3}$/.test(currency)) return `${amount} · ${label}`;

  const [, sign, coefficient, exponent = '0'] = match;
  const [whole, fraction = ''] = coefficient.split('.');
  const digits = whole + fraction;
  // Bound exponent parsing as well as expansion; the original representation always survives.
  const exponentDigits = exponent.replace(/^[+-]?0*/, '');
  const symbolic = () => `${amount} ${currency} (exact ${/[eE]/.test(amount!) ? 'scientific notation' : 'recorded notation'})`;
  if (exponentDigits.length > 6) return symbolic();
  const shift = Number(exponent); // Exponent only; the decimal amount is never a JS number.
  const point = whole.length + shift;
  const size = Math.max(point, digits.length) - Math.min(point, 0);
  if (size > MAX_EXPANDED_DIGITS) return symbolic();
  const expanded = point <= 0 ? `0.${'0'.repeat(-point)}${digits}`
    : point >= digits.length ? digits + '0'.repeat(point - digits.length)
      : `${digits.slice(0, point)}.${digits.slice(point)}`;
  const [integer, decimals] = expanded.split('.');
  const magnitude = BigInt(integer || '0');
  // Intl understands signed zero but BigInt does not, hence the literal -0 here.
  const signed = sign === '-' ? (magnitude === BigInt(0) ? -0 : -magnitude) : magnitude;
  let formatter: Intl.NumberFormat;
  try {
    formatter = new Intl.NumberFormat(locale, { style: 'currency', currency, currencyDisplay: 'code',
      minimumFractionDigits: 0, maximumFractionDigits: 0 });
  } catch {
    formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency, currencyDisplay: 'code',
      minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }
  const parts = formatter.formatToParts(signed);
  const decimalSeparator = new Intl.NumberFormat(formatter.resolvedOptions().locale)
    .formatToParts(1.1).find(part => part.type === 'decimal')?.value ?? '.';
  const lastInteger = parts.reduce((last, part, index) => part.type === 'integer' ? index : last, -1);
  return parts.map((part, index) => part.value
    + (index === lastInteger && decimals !== undefined ? decimalSeparator + decimals : '')).join('');
}

/** Require a recorded instant with an offset, not an ambiguous date/local wall-clock time. */
export function formatTimestamp(value: string | null | undefined, locale: string, timeZone: string): string {
  const unknown = `Time unknown${value ? ` (${value})` : ''}`;
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/);
  if (!match) return unknown;
  const [, y, m, d, h, min, sec, offset] = match;
  const year = Number(y), month = Number(m), day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month < 1 || month > 12 || day < 1 || day > days[month - 1]
    || Number(h) > 23 || Number(min) > 59 || Number(sec) > 59
    || offset !== 'Z' && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4)) > 59)) return unknown;
  const date = new Date(value!);
  if (!Number.isFinite(date.getTime())) return unknown;
  try {
    return new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', second: '2-digit', timeZone, timeZoneName: 'short' }).format(date);
  } catch { return `${value} · Time zone or locale unknown`; }
}
