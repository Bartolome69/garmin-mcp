// The same run from two sources: the twin of garmin_mcp/domain/dedupe.py, held to
// the same answers by garmin_mcp/domain/fixtures/dedupe.json. On the device so
// Source priority re-merges the moment the order changes or a pair is kept apart;
// the server re-merges with the same rule when it stores the change.
//
// Two runs from different sources are one when they start within a minute (as
// instants, so time zones and DST don't matter) and their distances are within 2%.
// The higher-priority source is kept; the other is hidden, never deleted.

export type Activity = { id: string; provider: string; start: string; distanceM: number; durationS?: number };
export type Merge = { kept: string; hidden: string };
export type Match = { startWithinSeconds: number; distanceWithinPct: number };

export const DEFAULT_MATCH: Match = { startWithinSeconds: 60, distanceWithinPct: 2 };

const at = (a: Activity) => Date.parse(a.start);

export function sameRun(a: Activity, b: Activity, m: Match = DEFAULT_MATCH): boolean {
  if (Math.abs(at(a) - at(b)) / 1000 > m.startWithinSeconds) return false;
  const longest = Math.max(a.distanceM, b.distanceM);
  if (longest <= 0) return false;
  return Math.abs(a.distanceM - b.distanceM) <= (longest * m.distanceWithinPct) / 100;
}

const pairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);

/** The runs to show and the merges made. Ties in priority keep the earlier start, then the lower id. */
export function merge(activities: Activity[], priority: string[], keptApart: [string, string][] = [], m: Match = DEFAULT_MATCH): {
  shown: Activity[]; merges: Merge[];
} {
  const rank = new Map(priority.map((p, i) => [p, i]));
  const apart = new Set(keptApart.map(([a, b]) => pairKey(a, b)));
  const r = (a: Activity) => rank.get(a.provider) ?? rank.size;
  const ordered = [...activities].sort((a, b) => r(a) - r(b) || at(a) - at(b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const shown: Activity[] = [];
  const merges: Merge[] = [];
  for (const a of ordered) {
    const twin = shown.find((k) => k.provider !== a.provider && !apart.has(pairKey(k.id, a.id)) && sameRun(k, a, m));
    if (twin) merges.push({ kept: twin.id, hidden: a.id });
    else shown.push(a);
  }
  return { shown: [...shown].sort((a, b) => at(a) - at(b)), merges };
}
