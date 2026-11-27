// Text to workout (build prompt, section 5.7): rule-based and on the device, so it
// works before sign-up and with the Coach off. It never guesses silently: anything
// it can't read comes back as a problem with the exact span and a fix-it suggestion.
//
// Grammar, clause by clause (clauses split on commas, "then", "+" and new lines):
//   N x QTY [at|@ TARGET] [with QTY RECOVERY]   repeats
//   QTY RECOVERY                                 after repeats: their recovery
//   QTY [at|@ TARGET | ZONE]                     a steady block
//   [QTY] warm-up | warm up | wu                 warm-up (QTY optional: 10 min)
//   [QTY] cool-down | cool down | cd             cool-down
// QTY:      a number with km, k, m, mi, min, s (or mm:ss for a time)
// TARGET:   a zone word (easy, steady, marathon, threshold, tempo, 10K, 5K, VO2),
//           a pace (4:05/km, 4:00-4:10, 6:30/mi) or a heart rate (150 bpm, 145-155 bpm)
// RECOVERY: easy, jog, rest, walk, recovery
//
// A session with any repeat or non-easy target gets a 10 min easy warm-up and
// cool-down when none was written, marked as added.

import { mmss } from './format';
import { ZONES, type Zone } from './paces';

export type Target =
  | { kind: 'zone'; zone: Zone }
  | { kind: 'pace'; fast: number; slow: number }   // seconds per km
  | { kind: 'hr'; low: number; high: number };

export type Size = { distanceM?: number; durationS?: number };

export type Step =
  | ({ kind: 'warmup' | 'cooldown'; added: boolean } & Size)
  | ({ kind: 'run'; target?: Target } & Size)
  | ({ kind: 'recovery'; how: Recovery } & Size)
  | { kind: 'repeat'; times: number; work: Extract<Step, { kind: 'run' }>; recovery?: Extract<Step, { kind: 'recovery' }> };

export type Recovery = 'easy' | 'jog' | 'rest' | 'walk';
export type Problem = { start: number; end: number; text: string; message: string; suggestion?: string };
export type Parsed = { steps: Step[]; readAs: string; problems: Problem[]; ok: boolean };

const ADDED_WARMUP_S = 600;
const MILE_M = 1609.344;

const ZONE_ALIASES: Record<string, Zone> = {
  easy: 'easy', steady: 'steady', marathon: 'marathon', mp: 'marathon', 'marathon pace': 'marathon',
  threshold: 'threshold', lt: 'threshold', tempo: 'tempo', '10k': '10k', '10k pace': '10k', '5k': '5k', '5k pace': '5k',
  vo2: 'vo2', vo2max: 'vo2', 'vo2 max': 'vo2',
};
const RECOVERIES: Record<string, Recovery> = { easy: 'easy', jog: 'jog', rest: 'rest', walk: 'walk', recovery: 'jog', rec: 'jog' };
const ZONE_PROSE: Record<Zone, string> = {
  easy: 'easy', steady: 'steady', marathon: 'marathon pace', threshold: 'threshold', tempo: 'tempo',
  '10k': '10K pace', '5k': '5K pace', vo2: 'VO2 pace',
};
const RECOVERY_WORDS: Record<Recovery, string> = { easy: 'easy jog', jog: 'jog', rest: 'rest', walk: 'walk' };
const SUGGEST_ZONE = 'Use a zone (easy, steady, marathon, threshold, tempo, 10K, 5K, VO2), a pace like 4:05/km or a heart rate like 150 bpm';
const SUGGEST_CLAUSE = 'Try "5 x 1 km at threshold", "2 min easy" or "10 min warm-up"';

const QTY = String.raw`(\d+(?:\.\d+)?|\d+:\d{2})\s*(km|k|kilometers?|kilometres?|m|meters?|metres?|mi|miles?|min|mins|minutes?|s|sec|secs|seconds?)?`;

// -- small readers -----------------------------------------------------------------

function readSize(num: string, unit: string | undefined): Size | { error: string } {
  if (num.includes(':')) {
    const [m, s] = num.split(':').map(Number);
    return unit && !/^(min|mins|minutes?)$/.test(unit) ? { error: `${num} looks like a time, not ${unit}` } : { durationS: m * 60 + s };
  }
  const n = Number(num);
  if (!unit) return { error: 'Add a unit: 1 km, 400 m, 3 min or 90 s' };
  if (/^(km|k|kilomet(er|re)s?)$/.test(unit)) return { distanceM: n * 1000 };
  if (/^(m|met(er|re)s?)$/.test(unit)) return { distanceM: n };
  if (/^(mi|miles?)$/.test(unit)) return { distanceM: Math.round(n * MILE_M) };
  if (/^(min|mins|minutes?)$/.test(unit)) return { durationS: Math.round(n * 60) };
  return { durationS: Math.round(n) };
}

function readPace(text: string): [number, number] | null {
  const m = /^(\d{1,2}):(\d{2})(?:\s*[-–]\s*(\d{1,2}):(\d{2}))?\s*(\/\s*(km|mi))?$/.exec(text.trim());
  if (!m) return null;
  const perMile = m[6] === 'mi';
  const toKm = (s: number) => Math.round(perMile ? s / 1.609344 : s);
  const a = toKm(+m[1] * 60 + +m[2]);
  const b = m[3] ? toKm(+m[3] * 60 + +m[4]) : a;
  return [Math.min(a, b), Math.max(a, b)];
}

function readTarget(raw: string): Target | { error: string; suggestion?: string } {
  let text = raw.trim().toLowerCase().replace(/\s+/g, ' ');
  if (!(text in ZONE_ALIASES) && text.endsWith(' pace')) text = text.slice(0, -5);
  const zone = ZONE_ALIASES[text];
  if (zone) return { kind: 'zone', zone };
  const pace = readPace(text);
  if (pace) return { kind: 'pace', fast: pace[0], slow: pace[1] };
  const hr = /^(?:hr\s*)?(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?\s*(?:bpm)?$/.exec(text);
  if (hr && (/bpm/.test(text) || /^hr/.test(text))) {
    const a = +hr[1], b = hr[2] ? +hr[2] : a;
    return { kind: 'hr', low: Math.min(a, b), high: Math.max(a, b) };
  }
  const near = closestZone(text);
  return { error: `"${raw.trim()}" isn't a zone, pace or heart rate`, suggestion: near ? `Did you mean "${near}"?` : SUGGEST_ZONE };
}

function closestZone(word: string): string | null {
  let best: [string, number] | null = null;
  for (const name of Object.keys(ZONE_ALIASES)) {
    const d = distance(word, name);
    if (d <= 2 && (!best || d < best[1])) best = [name, d];
  }
  return best ? best[0] : null;
}

function distance(a: string, b: string): number {
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
    dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  }
  return dp[a.length][b.length];
}

/** Short enough to be the recovery between repeats: 5 min or 800 m at most. */
const isShortRecovery = (s: Size) => (s.durationS ?? Infinity) <= 300 || (s.distanceM ?? Infinity) <= 800;

// -- words --------------------------------------------------------------------------

function sizeWords(s: Size): string {
  if (s.distanceM != null) return s.distanceM >= 1000 ? `${+(s.distanceM / 1000).toFixed(2)} km` : `${s.distanceM} m`;
  const secs = s.durationS ?? 0;
  if (secs % 60 === 0) return `${secs / 60} min`;
  return secs < 120 ? `${secs} s` : mmss(secs);
}

function targetWords(t?: Target): string {
  if (!t) return '';
  if (t.kind === 'zone') return ` at ${ZONE_PROSE[t.zone]}`;
  if (t.kind === 'pace') return t.fast === t.slow ? ` at ${mmss(t.fast)} /km` : ` at ${mmss(t.fast)}–${mmss(t.slow)} /km`;
  return t.low === t.high ? ` at ${t.low} bpm` : ` at ${t.low}–${t.high} bpm`;
}

export function stepWords(step: Step): string {
  switch (step.kind) {
    case 'warmup': return `${sizeWords(step)} warm-up${step.added ? ' (added)' : ''}`;
    case 'cooldown': return `${sizeWords(step)} cool-down${step.added ? ' (added)' : ''}`;
    case 'run': return `${sizeWords(step)}${targetWords(step.target)}`;
    case 'recovery': return `${sizeWords(step)} ${RECOVERY_WORDS[step.how]}`;
    case 'repeat': return [`${step.times} repeats`, stepWords(step.work), step.recovery && stepWords(step.recovery)]
      .filter(Boolean).join(' · ');
  }
}

// -- the parser ------------------------------------------------------------------------

type Clause = { text: string; start: number };

function clauses(input: string): Clause[] {
  const out: Clause[] = [];
  const re = /\s*(?:,|\bthen\b|\+|\n|;)\s*/gi;
  let last = 0;
  for (const m of input.matchAll(re)) {
    out.push({ text: input.slice(last, m.index), start: last });
    last = (m.index ?? 0) + m[0].length;
  }
  out.push({ text: input.slice(last), start: last });
  return out.map((c) => {
    const lead = c.text.length - c.text.trimStart().length;
    return { text: c.text.trim(), start: c.start + lead };
  }).filter((c) => c.text);
}

const RE_REPEAT = new RegExp(String.raw`^(\d+)\s*[x×]\s*${QTY}(?:\s*(?:at|@)\s*(.+?))?(?:\s+(?:with|w/)\s+${QTY}\s+(\w+))?$`, 'i');
const RE_RECOVERY = new RegExp(String.raw`^${QTY}\s+(easy|jog|rest|walk|recovery|rec)(?:\s+(?:jog|recovery))?$`, 'i');
const RE_WARM = new RegExp(String.raw`^(?:${QTY}\s+)?(warm[\s-]?up|wu|cool[\s-]?down|cd)(?:\s+${QTY})?$`, 'i');
const RE_BLOCK = new RegExp(String.raw`^${QTY}(?:\s*(?:at|@)\s*(.+)|\s+(.+))?$`, 'i');

function problemAt(c: Clause, sub: string | undefined, message: string, suggestion?: string): Problem {
  const i = sub ? c.text.toLowerCase().indexOf(sub.toLowerCase()) : -1;
  const start = c.start + (i >= 0 ? i : 0);
  const end = i >= 0 ? start + sub!.length : c.start + c.text.length;
  return { start, end, text: i >= 0 ? sub! : c.text, message, suggestion };
}

function isError<T extends object>(v: T | { error: string }): v is { error: string } {
  return 'error' in v;
}

export function parseWorkout(input: string): Parsed {
  const steps: Step[] = [];
  const problems: Problem[] = [];

  for (const c of clauses(input)) {
    let m: RegExpExecArray | null;
    if ((m = RE_REPEAT.exec(c.text))) {
      const [, times, num, unit, targetText, recNum, recUnit, recWord] = m;
      const size = readSize(num, unit?.toLowerCase());
      if (isError(size)) { problems.push(problemAt(c, `${num}${unit ?? ''}`, size.error)); continue; }
      let target: Target | undefined;
      if (targetText) {
        const t = readTarget(targetText);
        if ('error' in t) { problems.push(problemAt(c, targetText.trim(), t.error, t.suggestion)); continue; }
        target = t;
      }
      const repeat: Extract<Step, { kind: 'repeat' }> = { kind: 'repeat', times: +times, work: { kind: 'run', ...size, target } };
      if (recNum) {
        const rs = readSize(recNum, recUnit?.toLowerCase());
        const how = RECOVERIES[recWord.toLowerCase()];
        if (isError(rs) || !how) { problems.push(problemAt(c, recWord, 'That recovery isn\'t one Daash knows', 'Use easy, jog, rest or walk')); continue; }
        repeat.recovery = { kind: 'recovery', ...rs, how };
      }
      if (+times < 1 || +times > 50) { problems.push(problemAt(c, times, 'Between 1 and 50 repeats')); continue; }
      steps.push(repeat);
      continue;
    }
    if ((m = RE_RECOVERY.exec(c.text))) {
      const [, num, unit, word] = m;
      const size = readSize(num, unit?.toLowerCase());
      if (isError(size)) { problems.push(problemAt(c, `${num}${unit ?? ''}`, size.error)); continue; }
      const recovery: Extract<Step, { kind: 'recovery' }> = { kind: 'recovery', ...size, how: RECOVERIES[word.toLowerCase()] };
      const prev = steps[steps.length - 1];
      // "5x1km at threshold, 2 min easy": a short recovery right after repeats is theirs.
      // A long one ("6 x 20 s at 5K, 10 min easy") is a block of its own. "Read as" shows which.
      if (prev?.kind === 'repeat' && !prev.recovery && isShortRecovery(size)) {
        prev.recovery = recovery;
      } else if (recovery.how === 'easy') {
        steps.push({ kind: 'run', ...size, target: { kind: 'zone', zone: 'easy' } });
      } else {
        steps.push(recovery);
      }
      continue;
    }
    if ((m = RE_WARM.exec(c.text))) {
      const [, n1, u1, word, n2, u2] = m;
      const num = n1 ?? n2, unit = u1 ?? u2;
      const size: Size | { error: string } = num ? readSize(num, unit?.toLowerCase()) : { durationS: ADDED_WARMUP_S };
      if (isError(size)) { problems.push(problemAt(c, `${num}${unit ?? ''}`, size.error)); continue; }
      steps.push({ kind: /^(cool|cd)/i.test(word) ? 'cooldown' : 'warmup', ...size, added: false });
      continue;
    }
    if ((m = RE_BLOCK.exec(c.text))) {
      const [, num, unit, atText, zoneText] = m;
      const size = readSize(num, unit?.toLowerCase());
      if (isError(size)) { problems.push(problemAt(c, `${num}${unit ?? ''}`, size.error)); continue; }
      const raw = atText ?? zoneText;
      let target: Target | undefined;
      if (raw) {
        const t = readTarget(raw);
        if ('error' in t) { problems.push(problemAt(c, raw.trim(), t.error, t.suggestion)); continue; }
        target = t;
      }
      steps.push({ kind: 'run', ...size, target });
      continue;
    }
    const near = /^(\d+)\s*[x×]/.test(c.text) ? 'Write repeats as "5 x 1 km at threshold"' : SUGGEST_CLAUSE;
    problems.push(problemAt(c, undefined, `Daash couldn't read "${c.text}"`, near));
  }

  const quality = steps.some((s) => s.kind === 'repeat' || (s.kind === 'run' && s.target && !(s.target.kind === 'zone' && s.target.zone === 'easy')));
  if (quality && steps.length && !problems.length) {
    if (!steps.some((s) => s.kind === 'warmup')) steps.unshift({ kind: 'warmup', durationS: ADDED_WARMUP_S, added: true });
    if (!steps.some((s) => s.kind === 'cooldown')) steps.push({ kind: 'cooldown', durationS: ADDED_WARMUP_S, added: true });
  }
  const written = steps.filter((s) => !('added' in s && s.added));
  const readAs = written.length ? `Read as ${written.map(stepWords).join(' · ')}` : '';
  return { steps, readAs, problems, ok: steps.length > 0 && problems.length === 0 };
}

export { ZONES };
