// "Updated 2h ago" (build prompt, section 3): cached data shows its age instead of
// a spinner over content.

export const STALE_AFTER_MS = 30 * 60 * 1000;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export type Freshness = { stale: boolean; label: string };

/** How old the data is, in words. Fresh data shows the time it was read ("07:02"). */
export function freshness(fetchedAt: number | null, now: number): Freshness | null {
  if (fetchedAt == null) return null;
  const age = Math.max(0, now - fetchedAt);
  const at = new Date(fetchedAt);
  if (age < STALE_AFTER_MS) {
    return { stale: false, label: `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}` };
  }
  const minutes = Math.floor(age / 60000);
  if (minutes < 60) return { stale: true, label: `Updated ${minutes} min ago` };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { stale: true, label: `Updated ${hours}h ago` };
  if (hours < 48) return { stale: true, label: 'Updated yesterday' };
  return { stale: true, label: `Updated ${at.getDate()} ${MONTHS[at.getMonth()]}` };
}
