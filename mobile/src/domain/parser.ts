// Text to workout (build prompt, section 5.7): rule-based and on the device, so it
// works before sign-up and with the Coach off. It never guesses silently: anything
// it can't read comes back as a problem with the exact span and a fix-it suggestion,
// and everything it does read is said back in "Read as …".
//
// Grammar, clause by clause (clauses split on commas, "then", "+", ";" and new
// lines, never inside brackets):
//   N x QTY [at|@] [EFFORT] [[with|w/] QTY RECOVERY]   repeats
//   N x (QTY [EFFORT], QTY RECOVERY)                    repeats, bracketed
//   QTY RECOVERY | RECOVERY QTY                         after repeats: their recovery
//   QTY [at|@] EFFORT | EFFORT QTY                      a steady block
//   QTY with [the] last QTY at EFFORT                   easy, then the last part at EFFORT
//   hr N[-M] for QTY                                    a block at a heart rate
//   [QTY] warm-up | warm up | wu                        warm-up (QTY optional: 10 min)
//   [QTY] cool-down | cool down | cd                    cool-down
// QTY:      a number with km, k, m, mi, min, s, ' (min) or " (s), or mm:ss for a time
// EFFORT:   a zone word (easy, steady, marathon, threshold, tempo, 10K, 5K, VO2),
//           a pace (4:05/km, 4:00-4:10, 6:30/mi), a heart rate (150 bpm, hr 145-155),
//           "strides" (short and fast, no pace target), or "long run" (easy)
// RECOVERY: easy, jog, rest, walk, recovery
//
// A session with any repeat (strides aside) or non-easy target gets a 10 min easy
// warm-up and cool-down when none was written, marked as added; an easy run written
// first or last already does that job.

import { mmss } from './format';
import { ZONES, type Zone } from './paces';

export type Target =
  | { kind: 'zone'; zone: Zone }
  | { kind: 'pace'; fast: number; slow: number }   // seconds per km
  | { kind: 'hr'; low: number; high: number };

export type Size = { distanceM?: number; durationS?: number };

/** strides: short, fast and relaxed, with no pace target; long: the long run, at easy. */
export type RunLabel = 'strides' | 'long';

export type Step =
  | ({ kind: 'warmup' | 'cooldown'; added: boolean } & Size)
  | ({ kind: 'run'; target?: Target; label?: RunLabel } & Size)
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
const SUGGEST_HARD = 'For a hard effort name the zone (threshold, 10K, 5K or VO2) or a pace like 4:00/km';

/** Words that sound like a target but say no number: each gets its own fix-it, never a guessed zone. */
const VAGUE: Record<string, string> = {
  hard: SUGGEST_HARD, fast: SUGGEST_HARD, quick: SUGGEST_HARD, sprint: SUGGEST_HARD, sprints: SUGGEST_HARD,
  effort: SUGGEST_HARD, max: SUGGEST_HARD, 'all out': SUGGEST_HARD, moderate: 'Say steady or marathon pace, or a pace like 5:00/km',
  mile: '5K pace is the fastest zone; or give a pace like 3:45/km', 'mile pace': '5K pace is the fastest zone; or give a pace like 3:45/km',
  '1500m pace': '5K pace is the fastest zone; or give a pace like 3:40/km', '3k': '5K pace is the fastest zone; or give a pace like 3:55/km',
  '3k pace': '5K pace is the fastest zone; or give a pace like 3:55/km',
  'half marathon': 'Say threshold (close to half marathon pace) or a pace like 4:30/km',
  hm: 'Say threshold (close to half marathon pace) or a pace like 4:30/km', 'hm pace': 'Say threshold (close to half marathon pace) or a pace like 4:30/km',
  race: 'For a race effort give its pace, like "5 km at 5K pace"', parkrun: 'For a race effort give its pace, like "5 km at 5K pace"',
  'race pace': 'Give the race: 5K pace, 10K pace or marathon pace',
};

const UNIT_WORDS = ['km', 'k', 'kilometers', 'kilometres', 'kilometer', 'kilometre', 'm', 'meters', 'metres', 'meter', 'metre',
  'mi', 'mile', 'miles', 'min', 'mins', 'minute', 'minutes', 's', 'sec', 'secs', 'second', 'seconds'];
// Time first (so "1:00" isn't "1"), units longest first, and a unit can't run into a
// letter ("10 min" isn't "10 m" + "in"; "1kn" has no unit, and gets the typo fix-it).
const QTY = String.raw`(\d+:\d{2}|\d+(?:\.\d+)?)\s*(kilometers?|kilometres?|km|k|meters?|metres?|miles?|mi|minutes?|mins|min|m|seconds?|secs|sec|s|'|′|"|″)?(?![a-z])`;
const REC = '(easy|jog|rest|walk|recovery|rec)';

// -- small readers -----------------------------------------------------------------

function readSize(num: string, unit: string | undefined): Size | { error: string; suggestion?: string } {
  if (num.includes(':')) {
    const [m, s] = num.split(':').map(Number);
    return unit && !/^(min|mins|minutes?|')$/.test(unit) ? { error: `${num} looks like a time, not ${unit}` } : { durationS: m * 60 + s };
  }
  const n = Number(num);
  if (!unit) {
    return { error: 'Add a unit: 1 km, 400 m, 3 min or 90 s', suggestion: n >= 100 ? `Did you mean "${num} m"?` : undefined };
  }
  if (/^(km|k|kilomet(er|re)s?)$/.test(unit)) return { distanceM: n * 1000 };
  if (/^(m|met(er|re)s?)$/.test(unit)) return { distanceM: n };
  if (/^(mi|miles?)$/.test(unit)) return { distanceM: Math.round(n * MILE_M) };
  if (/^(min|mins|minutes?|'|′)$/.test(unit)) return { durationS: Math.round(n * 60) };
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

type Effort = { target?: Target; label?: RunLabel };
type Failure = { error: string; suggestion?: string };

/** A zone, pace, heart rate, "strides" or "long run"; or why not, with a fix-it. */
function readEffort(raw: string): Effort | Failure {
  let text = raw.trim().toLowerCase().replace(/\s+/g, ' ');
  if (/^strides?$/.test(text)) return { label: 'strides' };
  if (/^long( run)?( easy)?$|^easy long( run)?$/.test(text)) return { target: { kind: 'zone', zone: 'easy' }, label: 'long' };
  if (VAGUE[text]) return { error: `"${raw.trim()}" doesn't say how fast`, suggestion: VAGUE[text] };
  if (!(text in ZONE_ALIASES) && text.endsWith(' pace')) text = text.slice(0, -5);
  if (VAGUE[text]) return { error: `"${raw.trim()}" doesn't say how fast`, suggestion: VAGUE[text] };
  const zone = ZONE_ALIASES[text];
  if (zone) return { target: { kind: 'zone', zone } };
  const pace = readPace(text);
  if (pace) return { target: { kind: 'pace', fast: pace[0], slow: pace[1] } };
  const hr = /^(?:hr\s*|heart rate\s*)?(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?\s*(?:bpm)?$/.exec(text);
  if (hr && (/bpm/.test(text) || /^(hr|heart rate)/.test(text))) {
    const a = +hr[1], b = hr[2] ? +hr[2] : a;
    return { target: { kind: 'hr', low: Math.min(a, b), high: Math.max(a, b) } };
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
    case 'run':
      if (step.label === 'strides') return `${sizeWords(step)} strides`;
      if (step.label === 'long') return `${sizeWords(step)} long run at easy`;
      return `${sizeWords(step)}${targetWords(step.target)}`;
    case 'recovery': return `${sizeWords(step)} ${RECOVERY_WORDS[step.how]}`;
    case 'repeat': return [`${step.times} repeats`, stepWords(step.work), step.recovery && stepWords(step.recovery)]
      .filter(Boolean).join(' · ');
  }
}

// -- the parser ------------------------------------------------------------------------

type Clause = { text: string; start: number };

/** Clauses split on commas, "then", "+", ";" and new lines, but not inside brackets. */
function clauses(input: string, offset = 0): Clause[] {
  const out: Clause[] = [];
  let depth = 0, last = 0, i = 0;
  const push = (end: number) => {
    const raw = input.slice(last, end);
    const lead = raw.length - raw.trimStart().length;
    if (raw.trim()) out.push({ text: raw.trim(), start: offset + last + lead });
  };
  while (i < input.length) {
    const ch = input[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    if (depth === 0) {
      const m = /^(?:,|;|\+|\n|\s+then\s+)/i.exec(input.slice(i));
      if (m) { push(i); last = i + m[0].length; i = last; continue; }
    }
    i++;
  }
  push(input.length);
  return out;
}

const RE_REPEAT_HEAD = new RegExp(String.raw`^(\d+)\s*[x×]\s*${QTY}`, 'i');
const RE_REPEAT_BRACKETS = /^(\d+)\s*[x×]\s*\((.*)\)$/i;
const RE_TRAILING_RECOVERY = new RegExp(String.raw`(?:\s*(?:,|\bwith\b|\bw\/))?\s*${QTY}\s+${REC}(?:\s+(?:jog|recovery))?$`, 'i');
const RE_RECOVERY = new RegExp(String.raw`^${QTY}\s+${REC}(?:\s+(?:jog|recovery))?$`, 'i');
const RE_RECOVERY_FIRST = new RegExp(String.raw`^${REC}\s+(?:for\s+)?${QTY}$`, 'i');
const RE_WARM = new RegExp(String.raw`^(?:${QTY}\s+)?(warm[\s-]?up|wu|cool[\s-]?down|cd)(?:\s+${QTY})?$`, 'i');
const RE_LAST = new RegExp(String.raw`^(?:long(?:\s+run)?\s+)?${QTY}\s+(?:long(?:\s+run)?\s+)?with\s+(?:the\s+)?last\s+${QTY}\s*(?:at|@)\s*(.+)$`, 'i');
const RE_HR_FOR = new RegExp(String.raw`^(?:hr|heart rate)\s+(\d{2,3})(?:\s*[-–]\s*(\d{2,3}))?(?:\s*bpm)?\s+for\s+${QTY}$`, 'i');
const RE_BLOCK = new RegExp(String.raw`^${QTY}(?:\s*(?:at|@)\s*(.+)|\s+(.+))?$`, 'i');
const RE_EFFORT_FIRST = new RegExp(String.raw`^([a-z][a-z0-9 ]*?)\s+(?:for\s+)?${QTY}$`, 'i');

function problemAt(c: Clause, sub: string | undefined, message: string, suggestion?: string): Problem {
  const i = sub ? c.text.toLowerCase().indexOf(sub.toLowerCase()) : -1;
  const start = c.start + (i >= 0 ? i : 0);
  const end = i >= 0 ? start + sub!.length : c.start + c.text.length;
  return { start, end, text: i >= 0 ? sub! : c.text, message, suggestion };
}

function isError<T extends object>(v: T | Failure): v is Failure {
  return 'error' in v;
}

/** A unit that's one letter off a real one ("1kn"), for the fix-it. */
function unitTypo(c: Clause): Problem | null {
  for (const m of c.text.matchAll(/(\d+(?:\.\d+)?)\s*([a-z]{1,8})\b/gi)) {
    const word = m[2].toLowerCase();
    if (UNIT_WORDS.includes(word) || word === 'x' || ZONE_ALIASES[word] || RECOVERIES[word]) continue;
    const near = UNIT_WORDS.find((u) => u.length > 1 && distance(word, u) === 1);
    if (near) return problemAt(c, m[0], `"${m[0]}" isn't a unit Daash knows`, `Did you mean "${m[1]} ${near}"?`);
  }
  return null;
}

type Run = Extract<Step, { kind: 'run' }>;
type Rec = Extract<Step, { kind: 'recovery' }>;

function readRecovery(c: Clause, num: string, unit: string | undefined, word: string): Rec | Problem {
  const size = readSize(num, unit?.toLowerCase());
  if (isError(size)) return problemAt(c, `${num}${unit ?? ''}`, size.error, size.suggestion);
  const how = RECOVERIES[word.toLowerCase()];
  if (!how) return problemAt(c, word, 'That recovery isn\'t one Daash knows', 'Use easy, jog, rest or walk');
  return { kind: 'recovery', ...size, how };
}

function readRun(c: Clause, num: string, unit: string | undefined, effortText: string | undefined): Run | Problem {
  const size = readSize(num, unit?.toLowerCase());
  if (isError(size)) return problemAt(c, `${num}${unit ?? ''}`, size.error, size.suggestion);
  if (!effortText || !effortText.trim()) return { kind: 'run', ...size };
  const e = readEffort(effortText);
  if (isError(e)) return problemAt(c, effortText.trim(), e.error, e.suggestion);
  return { kind: 'run', ...size, ...e };
}

const isProblem = (v: object): v is Problem => 'message' in v;

function repeat(c: Clause, times: string, work: Run, recovery?: Rec): Step | Problem {
  if (+times < 1 || +times > 50) return problemAt(c, times, 'Between 1 and 50 repeats');
  return { kind: 'repeat', times: +times, work, ...(recovery ? { recovery } : {}) };
}

/** One clause: a step, a recovery for the repeat before it, or a problem. */
function readClause(c: Clause, prev: Step | undefined): Step | Rec | Problem {
  let m: RegExpExecArray | null;

  if ((m = RE_REPEAT_BRACKETS.exec(c.text))) {
    const inner = clauses(m[2], c.start + c.text.indexOf('(') + 1);
    if (inner.some((x) => /^\d+\s*[x×]/i.test(x.text))) {
      return problemAt(c, undefined, 'Repeats inside repeats aren\'t something Daash reads yet', 'Write each set out in turn, with the rest between sets');
    }
    if (!inner.length || inner.length > 2) return problemAt(c, undefined, 'In brackets, give the rep and its recovery', 'Like "4 x (1 km at 10K, 400 m jog)"');
    const w = RE_BLOCK.exec(inner[0].text);
    if (!w) return problemAt(inner[0], undefined, `Daash couldn't read "${inner[0].text}"`, 'Like "4 x (1 km at 10K, 400 m jog)"');
    const work = readRun(inner[0], w[1], w[2], w[3] ?? w[4]);
    if (isProblem(work)) return work;
    let recovery: Rec | undefined;
    if (inner[1]) {
      const r = RE_RECOVERY.exec(inner[1].text) ?? null;
      const rf = r ? null : RE_RECOVERY_FIRST.exec(inner[1].text);
      if (!r && !rf) return problemAt(inner[1], undefined, `Daash couldn't read "${inner[1].text}" as a recovery`, 'Like "400 m jog" or "2 min easy"');
      const got = r ? readRecovery(inner[1], r[1], r[2], r[3]) : readRecovery(inner[1], rf![2], rf![3], rf![1]);
      if (isProblem(got)) return got;
      recovery = got;
    }
    return repeat(c, m[1], work, recovery);
  }

  if ((m = RE_REPEAT_HEAD.exec(c.text))) {
    const [head, times, num, unit] = m;
    let rest = c.text.slice(head.length).trim();
    let recovery: Rec | undefined;
    const tail = RE_TRAILING_RECOVERY.exec(rest);
    if (tail && tail.index >= 0) {
      const got = readRecovery(c, tail[1], tail[2], tail[3]);
      if (isProblem(got)) return got;
      recovery = got;
      rest = rest.slice(0, tail.index).trim();
    }
    rest = rest.replace(/^(?:at|@)\s*/i, '');
    if (/^[a-z]{1,8}\b/i.test(rest) && !unit && !num.includes(':')) {
      // "5x1kn …": the unit runs into a word that isn't one.
      const typo = unitTypo(c);
      if (typo) return typo;
    }
    const work = readRun(c, num, unit, rest || undefined);
    if (isProblem(work)) return work;
    return repeat(c, times, work, recovery);
  }

  if ((m = RE_RECOVERY.exec(c.text)) || (m = RE_RECOVERY_FIRST.exec(c.text))) {
    const first = RE_RECOVERY_FIRST.test(c.text) && !RE_RECOVERY.test(c.text);
    const got = first ? readRecovery(c, m[2], m[3], m[1]) : readRecovery(c, m[1], m[2], m[3]);
    if (isProblem(got)) return got;
    // "5x1km at threshold, 2 min easy": a short recovery right after repeats is theirs.
    // A long one ("6 x 20 s at 5K, 10 min easy") is a block of its own. "Read as" shows which.
    if (prev?.kind === 'repeat' && !prev.recovery && isShortRecovery(got)) return got;
    const { kind, how, ...size } = got;
    return how === 'easy' ? { kind: 'run', ...size, target: { kind: 'zone', zone: 'easy' } } : got;
  }

  if ((m = RE_WARM.exec(c.text))) {
    const [, n1, u1, word, n2, u2] = m;
    const num = n1 ?? n2, unit = u1 ?? u2;
    const size: Size | Failure = num ? readSize(num, unit?.toLowerCase()) : { durationS: ADDED_WARMUP_S };
    if (isError(size)) return problemAt(c, `${num}${unit ?? ''}`, size.error, size.suggestion);
    return { kind: /^(cool|cd)/i.test(word) ? 'cooldown' : 'warmup', ...size, added: false };
  }

  if ((m = RE_LAST.exec(c.text))) {
    return problemAt(c, undefined, 'internal: handled by readLast');
  }

  if ((m = RE_HR_FOR.exec(c.text))) {
    const size = readSize(m[3], m[4]?.toLowerCase());
    if (isError(size)) return problemAt(c, `${m[3]}${m[4] ?? ''}`, size.error, size.suggestion);
    const a = +m[1], b = m[2] ? +m[2] : a;
    return { kind: 'run', ...size, target: { kind: 'hr', low: Math.min(a, b), high: Math.max(a, b) } };
  }

  if ((m = RE_BLOCK.exec(c.text))) {
    const [, num, unit, atText, plainText] = m;
    if (!unit && !num.includes(':') && plainText && /^[a-z]{1,8}\b/i.test(plainText)) {
      const typo = unitTypo(c);
      if (typo) return typo;
    }
    return readRun(c, num, unit, atText ?? plainText);
  }

  if ((m = RE_EFFORT_FIRST.exec(c.text))) {
    const [, words, num, unit] = m;
    const e = readEffort(words);
    if (!isError(e)) return readRun(c, num, unit, words);
    if (/^fartlek$/i.test(words.trim())) {
      return problemAt(c, words, 'Fartlek is a mix of paces', 'Write the pieces, like "10 x 1 min at 5K pace, 1 min easy"');
    }
    return problemAt(c, words.trim(), e.error, e.suggestion);
  }

  if (/^(easy|steady|long|recovery|tempo|threshold)( run)?$/i.test(c.text)) {
    return problemAt(c, undefined, `How long is the ${c.text.toLowerCase()}?`, `Give it a time or distance, like "40 min ${c.text.toLowerCase().replace(/ run$/, '')}" or "8 km ${c.text.toLowerCase().replace(/ run$/, '')}"`);
  }
  if (/^fartlek\b/i.test(c.text)) {
    return problemAt(c, undefined, 'Fartlek is a mix of paces', 'Write the pieces, like "10 x 1 min at 5K pace, 1 min easy"');
  }
  const typo = unitTypo(c);
  if (typo) return typo;
  const near = /^(\d+)\s*[x×]/.test(c.text) ? 'Write repeats as "5 x 1 km at threshold"' : SUGGEST_CLAUSE;
  return problemAt(c, undefined, `Daash couldn't read "${c.text}"`, near);
}

/** "28 km with the last 8 at marathon": the first part easy, the last part at the effort. */
function readLast(c: Clause): Step[] | Problem | null {
  const m = RE_LAST.exec(c.text);
  if (!m) return null;
  const [, n1, u1, n2, u2, effortText] = m;
  const whole = readSize(n1, u1?.toLowerCase());
  if (isError(whole)) return problemAt(c, `${n1}${u1 ?? ''}`, whole.error, whole.suggestion);
  const last = readSize(n2, (u2 ?? u1)?.toLowerCase());
  if (isError(last)) return problemAt(c, `${n2}${u2 ?? ''}`, last.error, last.suggestion);
  const e = readEffort(effortText);
  if (isError(e)) return problemAt(c, effortText.trim(), e.error, e.suggestion);
  const sameKind = (whole.distanceM != null) === (last.distanceM != null);
  const firstPart = whole.distanceM != null ? (whole.distanceM - (last.distanceM ?? 0)) : (whole.durationS ?? 0) - (last.durationS ?? 0);
  if (!sameKind || firstPart <= 0) {
    return problemAt(c, undefined, 'The last part has to be shorter than the whole run, in the same unit', 'Like "28 km with the last 8 km at marathon"');
  }
  const first: Step = whole.distanceM != null
    ? { kind: 'run', distanceM: firstPart, target: { kind: 'zone', zone: 'easy' } }
    : { kind: 'run', durationS: firstPart, target: { kind: 'zone', zone: 'easy' } };
  return [first, { kind: 'run', ...last, ...e }];
}

export function parseWorkout(input: string): Parsed {
  const steps: Step[] = [];
  const problems: Problem[] = [];

  for (const c of clauses(input)) {
    const last = readLast(c);
    if (last) {
      if (Array.isArray(last)) steps.push(...last); else problems.push(last);
      continue;
    }
    const got = readClause(c, steps[steps.length - 1]);
    if (isProblem(got)) { problems.push(got); continue; }
    if (got.kind === 'recovery') {
      const prev = steps[steps.length - 1];
      if (prev?.kind === 'repeat' && !prev.recovery && isShortRecovery(got)) { prev.recovery = got; continue; }
    }
    steps.push(got);
  }

  // Quality is any repeat (strides aside) or any block faster than easy. An easy run at
  // the start or end already is the warm-up or cool-down, so nothing is added there.
  const isEasy = (s?: Step) => s?.kind === 'run' && s.label !== 'strides' && s.target?.kind === 'zone' && s.target.zone === 'easy';
  const quality = steps.some((s) => (s.kind === 'repeat' && s.work.label !== 'strides')
    || (s.kind === 'run' && s.label !== 'strides' && !isEasy(s)));
  if (quality && !problems.length) {
    if (!steps.some((s) => s.kind === 'warmup') && !isEasy(steps[0])) steps.unshift({ kind: 'warmup', durationS: ADDED_WARMUP_S, added: true });
    if (!steps.some((s) => s.kind === 'cooldown') && !isEasy(steps[steps.length - 1])) steps.push({ kind: 'cooldown', durationS: ADDED_WARMUP_S, added: true });
  }
  const written = steps.filter((s) => !('added' in s && s.added));
  const readAs = written.length ? `Read as ${written.map(stepWords).join(' · ')}` : '';
  return { steps, readAs, problems, ok: steps.length > 0 && problems.length === 0 };
}

export { ZONES };
