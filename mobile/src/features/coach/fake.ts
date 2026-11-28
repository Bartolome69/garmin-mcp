// A stand-in Coach for development builds and tests: the pilot's API shape, scripted.
// Its replies are the design's persona, and every number in them is one the persona's
// fixtures already hold (Today's readiness, Wednesday's run, the plan), so the dev
// build shows what the server's Coach would: numbers from the data, observations only.
// The chart comes with the reply as the server will send it, built from the fixture's
// numbers, not written by a model.
import { Offline } from '../../api/client';
import { PERSONA_TODAY } from '../today/fixture';
import RUNS from '../run/runs.fixture.json';
import type { RunVM } from '../run/model';
import { CoachLimitReached, type CoachApi, type SendBody, type ThreadAnswer } from './api';
import type { InlineChart, ServerMessage } from './model';
import { ERROR_WORDS, type CoachEvent } from './sse';

export const STARTERS = ['Why Back off?', 'How’s my week?', 'Should I still do the tempo?', 'Make it easier'];

const hrv = PERSONA_TODAY.readiness.signals.find((s) => s.key === 'hrv')!.sheet;
const last7 = hrv.series.slice(-7) as number[];
const NIGHTS = ['Sat', 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
const band = hrv.band!;
const below = last7.map((v, i) => (v < band[0] ? i : -1)).filter((i) => i >= 0);

export const HRV_CHART: InlineChart = {
  kind: 'line', title: 'HRV, last 7 nights', unit: 'ms', caption: `range ${band[0]}–${band[1]}`,
  labels: NIGHTS, values: last7, band, highlight: below,
  summary: `HRV, last 7 nights: ${last7.join(', ')} ms. Below your range of ${band[0]} to ${band[1]} on the last ${below.length}.`,
};

const run = (RUNS as unknown as { cases: Record<string, RunVM> }).cases['2026-11-25-intervals'];
const reps = run.laps.filter((l) => l.kind === 'work');
const peaks = reps.map((l) => Math.max(...run.series.km.map((k, i) => (k >= l.fromKm && k <= l.toKm ? run.series.hr[i] ?? 0 : 0))));

export const REP_HR_CHART: InlineChart = {
  kind: 'bars', title: 'Peak heart rate per rep', unit: 'bpm', caption: 'bpm',
  labels: reps.map((l) => l.label), values: peaks, band: null, highlight: [peaks.length - 1],
  summary: `Peak heart rate per rep: ${peaks.join(', ')} bpm.`,
};

type Script = { tools: [string, string][]; text: string; chart?: InlineChart; pending?: { tool: string; text: string; request: { kind: 'easier' } }; memory?: { kind: string; text: string } };

const last = peaks.length - 1;
/** The persona's replies, chosen by what's asked. */
export function scriptFor(body: SendBody): Script {
  const t = body.text.toLowerCase();
  if (body.context?.kind === 'run' || /\brep\b|wednesday|run club/.test(t)) {
    return { tools: [['get_activity_details', 'Looking at the run']], chart: REP_HR_CHART,
      text: `Rep pace averaged 4:01. Your peak heart rate on ${reps[last].label.toLowerCase()} was ${peaks[last]}, ${peaks[last] - peaks[last - 1]} bpm above ${reps[last - 1].label.toLowerCase()}.` };
  }
  if (/easier|make it/.test(t)) {
    return { tools: [['get_plan', 'Reading your plan']],
      text: 'I’ve proposed making today easier: the tempo becomes Easy 6 km. It’s waiting for your confirm.',
      pending: { tool: 'easier', text: 'Make today easier: 3 × 2 km at threshold becomes Easy 6 km.', request: { kind: 'easier' } } };
  }
  if (/travel/.test(t)) {
    return { tools: [], memory: { kind: 'travel', text: 'Travelling 1 to 3 Dec' },
      text: 'Saved to memory: travelling 1 to 3 Dec. The plan for those days stays as it is until you ask to change it.' };
  }
  if (/back off|tempo|why|hrv|sleep/.test(t) || body.context?.kind === 'readiness') {
    return { tools: [['get_readiness', 'Checking readiness'], ['get_sleep_data', 'Checking sleep']], chart: HRV_CHART,
      text: `Your call. HRV has been under your range of ${band[0]}–${band[1]} ms for two nights: ${last7.slice(-2).join(' and ')} ms. Sleep was 4:45, 2h under your usual.` };
  }
  return { tools: [['get_plan', 'Reading your plan']],
    text: `This week you’ve run ${PERSONA_TODAY.week.doneKm} of ${PERSONA_TODAY.week.plannedKm} km planned. The tempo today and the long run on Sunday are what’s left.` };
}

export type FakeCoachOptions = {
  /** The thread the server starts with. */
  messages?: ServerMessage[];
  starters?: string[];
  /**
   * What the next sends do, in order: 'offline' (never reached Daash), 'cap' (the 429), 'error' (the stream's
   * error event), 'drop' (the server stored the question and began the reply, then the connection dropped).
   */
  failures?: Failure[];
  /** Hold every reply after its first words until release(): to see a reply streaming. */
  hold?: boolean;
  /** No answer at all for the thread (offline on open). */
  offlineThread?: boolean;
  /** /new fails (offline): the thread stays on the server. */
  clearFails?: boolean;
};

type Failure = 'offline' | 'cap' | 'error' | 'drop';

export class FakeCoach implements CoachApi {
  messages: ServerMessage[];
  starters: string[];
  sent: SendBody[] = [];
  decisions: [string, string][] = [];
  feedbacks: [string, string][] = [];
  cleared = 0;
  private failures: Failure[];
  private nextId: number;
  private hold: boolean;
  private releases: (() => void)[] = [];
  offlineThread: boolean;
  clearFails: boolean;

  constructor(o: FakeCoachOptions = {}) {
    this.messages = [...(o.messages ?? [])];
    this.starters = o.starters ?? STARTERS;
    this.failures = [...(o.failures ?? [])];
    this.hold = !!o.hold;
    this.offlineThread = !!o.offlineThread;
    this.clearFails = !!o.clearFails;
    this.nextId = 100 + this.messages.length;
  }

  /** Lets held replies finish. */
  release(): void { const r = this.releases; this.releases = []; r.forEach((f) => f()); }

  async thread(): Promise<ThreadAnswer> {
    if (this.offlineThread) throw new Offline();
    return { messages: this.messages.map((m) => ({ ...m })), starters: this.starters };
  }

  async send(body: SendBody, onEvent: (e: CoachEvent) => void): Promise<void> {
    this.sent.push(body);
    const failure = this.failures.shift();
    if (failure === 'offline') throw new Offline();
    if (failure === 'cap') throw new CoachLimitReached('That’s today’s limit of 10 messages. It resets at midnight.');
    const s = scriptFor(body);
    const userId = String(this.nextId++);
    const id = String(this.nextId++);
    this.messages.push({ id: userId, role: 'user', text: body.text, status: 'sent' });
    onEvent({ type: 'start', userId, id });
    for (const [name, label] of s.tools) onEvent({ type: 'tool', name, label });
    if (failure === 'error') {
      onEvent({ type: 'delta', text: s.text.split('. ')[0] + '.' });
      this.messages.push({ id, role: 'assistant', text: s.text.split('. ')[0] + '.', status: 'partial' });
      onEvent({ type: 'error', code: 'provider', message: ERROR_WORDS.provider });
      return;
    }
    const words = s.text.split(' ');
    const half = Math.max(1, Math.floor(words.length / 2));
    onEvent({ type: 'delta', text: `${words.slice(0, half).join(' ')} ` });
    if (failure === 'drop') {
      this.messages.push({ id, role: 'assistant', text: words.slice(0, half).join(' '), status: 'partial' });
      throw new Offline();
    }
    if (this.hold) await new Promise<void>((r) => this.releases.push(r));
    onEvent({ type: 'delta', text: words.slice(half).join(' ') });
    if (s.chart) onEvent({ type: 'chart', chart: s.chart });
    if (s.memory) onEvent({ type: 'memory', id: `m${this.nextId++}`, ...s.memory });
    this.messages.push({ id, role: 'assistant', text: s.text, status: 'done' });
    if (s.pending) {
      const pid = String(this.nextId++);
      this.messages.push({ id: pid, role: 'action', text: s.pending.text, status: 'pending', tool: s.pending.tool });
      onEvent({ type: 'pending', id: pid, tool: s.pending.tool, text: s.pending.text, status: 'pending', proposal: null, request: s.pending.request });
    }
    onEvent({ type: 'done', id, status: 'done' });
  }

  async decide(id: string, decision: string) {
    this.decisions.push([id, decision]);
    const m = this.messages.find((x) => String(x.id) === id);
    if (m) m.status = 'declined';
    return { status: 'declined', message: 'Cancelled. Nothing changed.' };
  }

  async feedback(id: string, value: 'up' | 'down') {
    this.feedbacks.push([id, value]);
    const m = this.messages.find((x) => String(x.id) === id);
    if (m) m.feedback = value;
  }

  async clear() {
    if (this.clearFails) throw new Offline();
    this.cleared += 1;
    this.messages = [];
  }
}

/** The persona's thread in a development build: one exchange about Wednesday's run, two days ago. */
export function devThread(now: number): ServerMessage[] {
  const at = Math.floor(now / 1000) - 2 * 86_400;
  const q: SendBody = { text: 'Why did rep 6 feel so much harder?', context: { kind: 'run', id: '2026-11-25-intervals', label: 'About · Wed 25 · 6 × 800 m' } };
  const s = scriptFor(q);
  return [
    { id: 1, role: 'user', text: q.text, status: 'sent', at, context: q.context },
    { id: 2, role: 'assistant', text: s.text, status: 'done', at: at + 20, chart: s.chart, sources: ['Run · Garmin'] },
  ];
}
