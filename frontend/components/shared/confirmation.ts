/** Call only inside a user event handler, never render/effect. Keeps native confirm/CDP semantics. */
export function confirmAction(message: string): boolean {
  return typeof window !== 'undefined' && window.confirm(message);
}
