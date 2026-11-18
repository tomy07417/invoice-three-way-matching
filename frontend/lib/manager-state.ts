import type { ParameterUpdate, ReviewHistoryEvent, RuleValues } from './contracts';

// Canonical coefficient/exponent arithmetic: never round a Decimal through a JS number.
// Even enormous exponents stay symbolic rather than allocating exponent-sized strings.
function decimal(raw: string): { digits: string; exponent: bigint } {
  const match = /^([+-]?)(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?$/.exec(raw.trim());
  if (!match) throw new Error('Enter a finite decimal ≥ 0, without commas.');
  const [whole, fraction = ''] = match[2].split('.');
  let digits = (whole + fraction).replace(/^0+/, '');
  if (!digits) return { digits: '0', exponent: BigInt(0) };
  if (match[1] === '-') throw new Error('Enter a finite decimal ≥ 0, without commas.');
  let exponent = BigInt(match[3] ?? '0') - BigInt(fraction.length);
  const trimmed = digits.replace(/0+$/, '');
  exponent += BigInt(digits.length - trimmed.length); digits = trimmed;
  return { digits, exponent };
}
function format(value: { digits: string; exponent: bigint }): string {
  const { digits, exponent } = value;
  if (digits === '0') return '0';
  if (exponent === BigInt(0)) return digits;
  if (exponent > BigInt(0) && exponent <= BigInt(20)) {
    let zeros = ''; for (let i = BigInt(0); i < exponent; i++) zeros += '0';
    return digits + zeros;
  }
  const point = BigInt(digits.length) + exponent;
  if (exponent < BigInt(0) && point > BigInt(-6)) {
    if (point <= BigInt(0)) {
      let zeros = ''; for (let i = point; i < BigInt(0); i++) zeros += '0';
      return `0.${zeros}${digits}`;
    }
    // The point is bounded by the existing coefficient length, never by the exponent.
    let left = '', right = '';
    for (let i = 0; i < digits.length; i++) (BigInt(i) < point ? left += digits[i] : right += digits[i]);
    return `${left}.${right}`;
  }
  return `${digits}e${exponent}`;
}
export function fractionToPercent(raw: string): string {
  const value = decimal(raw); return format({ ...value, exponent: value.exponent + BigInt(2) });
}
export function percentToFraction(raw: string): string {
  const value = decimal(raw); return format({ ...value, exponent: value.exponent - BigInt(2) });
}
export function decimalEqual(left: string, right: string): boolean {
  const a = decimal(left), b = decimal(right);
  return a.digits === b.digits && a.exponent === b.exponent;
}
export interface ParameterDraft { tolerancePercent: string; analystLimit: string; comment: string }
export function parameterDraft(values: RuleValues): ParameterDraft {
  return { tolerancePercent: fractionToPercent(values.price_tolerance), analystLimit: values.analyst_approval_limit, comment: '' };
}
export function validateParameters(draft: ParameterDraft, current: RuleValues): {
  errors: Partial<Record<keyof ParameterDraft, string>>; values: ParameterUpdate | null; changed: boolean;
} {
  const errors: Partial<Record<keyof ParameterDraft, string>> = {};
  const values: ParameterUpdate = {};
  try {
    const next = percentToFraction(draft.tolerancePercent);
    if (!decimalEqual(next, current.price_tolerance)) values.price_tolerance = next;
  } catch (failure) { errors.tolerancePercent = (failure as Error).message; }
  try {
    decimal(draft.analystLimit);
    if (!decimalEqual(draft.analystLimit, current.analyst_approval_limit)) values.analyst_approval_limit = draft.analystLimit.trim();
  } catch (failure) { errors.analystLimit = (failure as Error).message; }
  if (Object.keys(errors).length) return { errors, values: null, changed: false };
  const changed = Object.keys(values).length > 0;
  if (changed && draft.comment.trim()) values.comment = draft.comment.trim();
  return { errors, values, changed };
}
export function requiresParameterRefresh(failure: unknown): boolean {
  const error = failure as { status?: number; code?: string } | null;
  return error?.status === undefined || error.code === 'invalid_response' || error.status === 0
    || error.status === 409 || error.status === 428 || error.status >= 500;
}
export function escalationComments(events: ReviewHistoryEvent[]): ReviewHistoryEvent[] {
  return events.filter(event => event.kind === 'escalation');
}
