// Small formatters shared by the on-device engines, matching garmin_mcp/domain/model.py.

/** Rounds .5 up, like Python's round_half_up on the server, so both agree to the second. */
export const roundHalfUp = (x: number): number => Math.floor(x + 0.5);

export function mmss(seconds: number): string {
  const total = roundHalfUp(seconds);
  const pad = (n: number) => String(n).padStart(2, '0');
  if (total >= 3600) return `${Math.floor(total / 3600)}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
  return `${Math.floor(total / 60)}:${pad(total % 60)}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** A calendar date as YYYY-MM-DD, read without time zones: a date is a date. */
export function parseDate(iso: string): { y: number; m: number; d: number } {
  const [y, m, d] = iso.split('-').map(Number);
  return { y, m, d };
}

export function shortDate(iso: string): string {
  const { m, d } = parseDate(iso);
  return `${d} ${MONTHS[m - 1]}`;
}

/** Monday = 0 … Sunday = 6, as Python's date.weekday(). */
export function weekday(iso: string): number {
  const { y, m, d } = parseDate(iso);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

export function addDays(iso: string, n: number): string {
  const { y, m, d } = parseDate(iso);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
