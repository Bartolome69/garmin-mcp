// Connections and source priority's view model (build prompt, section 5.12): what
// the server works out (garmin_mcp/domain/connections.py view()). The merges are
// re-made on the device by the de-dupe twin (domain/dedupe.ts) when the runner
// reorders sources or keeps a pair apart, so the screen answers at once; the server
// stores the change and re-merges with the same rule.
import { DAYS, weekday } from '../../domain/format';
import { merge, type Activity, type Match } from '../../domain/dedupe';

export type Route = 'direct' | 'intervals' | 'health';
export type Provider = {
  key: string;
  name: string;                  // "Garmin", "COROS", "Apple Watch"
  route: Route;
  state: 'live' | 'reconnect';
  status: string;                // "Connected to Garmin · Forerunner 965 · synced 07:02"
  caps: { label: string; on: boolean }[];
  reads: string;                 // "Activities, sleep, HRV"
  reconnectUrl: string | null;
};
export type MergeRow = {
  id: string; kept: string; hidden: string; keptProvider: string; hiddenProvider: string;
  date: string; title: string; gapSeconds: number; distancePct: number;
};
export type ConnectionsVM = {
  providers: Provider[];
  priority: string[];
  activities: Activity[];
  keptApart: [string, string][];
  merges: MergeRow[];
  coming: string[];
  match: Match;
};

/** The phone's health store has the phone's name for it. */
export function routeName(route: Route, os: string): string {
  if (route === 'intervals') return 'Intervals.icu';
  if (route === 'health') return os === 'ios' ? 'Apple Health' : 'Health Connect';
  return '';
}

export function badgeWords(p: Provider, os: string): string {
  return p.route === 'direct' ? 'Live' : `Via ${routeName(p.route, os)}`;
}

/** How a source reads in the priority list and the merges: "Garmin", "Apple Health", "COROS via Intervals.icu". */
export function sourceName(p: Pick<Provider, 'name' | 'route'>, os: string): string {
  if (p.route === 'health') return routeName('health', os);
  if (p.route === 'intervals') return `${p.name} via Intervals.icu`;
  return p.name;
}

const round1 = (x: number) => Math.round(x * 10) / 10;

/** The merges as rows, newest first: the twin of connections.py merges_json. */
export function mergeRows(vm: Pick<ConnectionsVM, 'activities' | 'match'>, priority: string[], keptApart: [string, string][]): MergeRow[] {
  const byId = new Map(vm.activities.map((a) => [a.id, a]));
  return merge(vm.activities, priority, keptApart, vm.match).merges.map(({ kept, hidden }) => {
    const k = byId.get(kept)!, h = byId.get(hidden)!;
    const date = k.start.slice(0, 10);
    return {
      id: `${kept}|${hidden}`, kept, hidden, keptProvider: k.provider, hiddenProvider: h.provider, date,
      title: `${DAYS[weekday(date)]} ${Number(date.slice(8, 10))} · ${(k.distanceM / 1000).toFixed(1)} km`,
      gapSeconds: Math.round(Math.abs(Date.parse(k.start) - Date.parse(h.start)) / 1000),
      distancePct: round1((Math.abs(k.distanceM - h.distanceM) / Math.max(k.distanceM, h.distanceM)) * 100),
    };
  }).sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

/** The view with the runner's own order and kept-apart pairs applied. */
export function withChanges(vm: ConnectionsVM, priority: string[] | null, keptApart: [string, string][] | null): ConnectionsVM {
  const order = priority ? [...priority.filter((k) => vm.priority.includes(k)), ...vm.priority.filter((k) => !priority.includes(k))] : vm.priority;
  const apart = keptApart ?? vm.keptApart;
  if (!priority && !keptApart) return vm;
  return { ...vm, priority: order, keptApart: apart, merges: mergeRows(vm, order, apart) };
}

/** "Also came from Apple Health. Started 12 s apart, distances 0.3% apart. Hidden, not deleted." */
export function mergeWords(m: MergeRow, vm: ConnectionsVM, os: string): string {
  const p = vm.providers.find((x) => x.key === m.hiddenProvider);
  const from = p ? sourceName(p, os) : m.hiddenProvider;
  return `Also came from ${from}. Started ${m.gapSeconds} s apart, distances ${m.distancePct}% apart. Hidden, not deleted.`;
}

/** The Source priority row: "Garmin first · 2 duplicate runs merged". */
export function prioritySummary(vm: ConnectionsVM, os: string): string {
  if (!vm.providers.length) return 'Nothing connected yet';
  const first = vm.providers.find((p) => p.key === vm.priority[0]);
  const n = vm.merges.length;
  return `${first ? sourceName(first, os) : 'No source'} first · ${n ? `${n} duplicate run${n === 1 ? '' : 's'} merged` : 'no duplicates this month'}`;
}

/** The You tab's row: "Connected to Garmin". */
export function youSummary(vm: ConnectionsVM): string {
  const live = vm.providers.filter((p) => p.state === 'live' && p.route === 'direct');
  return live.length ? `Connected to ${live.map((p) => p.name).join(', ')}` : 'Nothing connected yet';
}

export function mergeTitle(id: string, vm: ConnectionsVM): string {
  const [kept] = id.split('|');
  const a = vm.activities.find((x) => x.id === kept);
  if (!a) return id;
  const date = a.start.slice(0, 10);
  return `${DAYS[weekday(date)]} ${Number(date.slice(8, 10))} · ${(a.distanceM / 1000).toFixed(1)} km`;
}
