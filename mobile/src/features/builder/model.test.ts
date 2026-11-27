import { describe, expect, it } from '@jest/globals';

import { parseWorkout, stepWords } from '../../domain/parser';
import { PERSONA_PLAN } from '../plan/fixture';
import { allSessions, findSession } from '../plan/model';
import { applyToPlan } from '../watch/apply';
import { changesPlan } from '../watch/model';
import { adjustLine, build, buildProposal, dayChoices, durationWords, firstFreeDay, pacesFor, sessionText, stepsText, type WorkoutStep } from './model';

const basis = PERSONA_PLAN.basis;
const built = (text: string, step = 0) => build(parseWorkout(text), { basis, step, date: '2026-12-04' })!;

describe('build: what the builder shows', () => {
  it('the design\'s session: type, title, estimate and the steps as the session detail draws them', () => {
    const b = built('5x1km at threshold, 2 min easy');
    expect(b).toMatchObject({ type: 'tempo', typeLabel: 'Tempo', title: '5 × 1 km at threshold', estimate: '10.1 km · about 52 min',
      sub: 'Tempo · 10.1 km · 4:23–4:28 /km', targets: '4:23–4:28 /km', zone: 'threshold' });
    const z = pacesFor(basis, 0).zones;
    expect(b.detail.steps).toEqual([
      { kind: 'warmup', name: 'Warm-up', detail: '10 min easy (added)', target: { kind: 'pace', zone: 'easy', range: z.easy } },
      { kind: 'repeat', times: 5, steps: [
        { kind: 'work', name: '1 km', detail: 'Threshold', target: { kind: 'pace', zone: 'threshold', range: z.threshold } },
        { kind: 'recovery', name: '2 min', detail: 'Easy jog', target: { kind: 'none' } },
      ] },
      { kind: 'cooldown', name: 'Cool-down', detail: '10 min easy (added)', target: { kind: 'pace', zone: 'easy', range: z.easy } },
    ]);
    expect(b.detail.meta).toBe('Fri 4 Dec · 10.1 km · about 52 min');
    expect(b.detail.adjustable).toBe(true);
  });

  it.each([
    ['10 x 400m @ 5k pace w/ 200m jog', 'Intervals', '10 × 400 m at 5K pace'],
    ['6x800m at 3:50/km 400m jog', 'Intervals', '6 × 800 m at 3:50 /km'],
    ['5 x 1000m', 'Intervals', '5 × 1 km'],
    ['30 min steady', 'Tempo', '30 min at steady'],
    ['45 min at 140-150 bpm', 'Tempo', '45 min at 140–150 bpm'],
    ['40 min easy', 'Easy', 'Easy 40 min'],
    ['8 km easy + 6 x 100 m strides', 'Easy', 'Easy 8 km + strides'],
    ['16 km long run', 'Long', 'Long run 16 km'],
    ['long run 28 km with last 8 at marathon', 'Long', 'Long run 28 km, 8 km at marathon pace'],
    ['20 km easy', 'Long', 'Long run 20 km'],
  ])('%s: the %s chip, titled "%s"', (text, label, title) => {
    const b = built(text);
    expect(b.typeLabel).toBe(label);
    expect(b.title).toBe(title);
  });

  it('nothing until the text reads cleanly', () => {
    expect(build(parseWorkout('6 x 400m hard'), { basis, step: 0, date: '2026-12-04' })).toBeNull();
    expect(build(parseWorkout(''), { basis, step: 0, date: '2026-12-04' })).toBeNull();
  });

  it('times over an hour read as hours and minutes', () => {
    expect(durationWords(46.4)).toBe('46 min');
    expect(durationWords(105)).toBe('1 h 45 min');
    expect(durationWords(120)).toBe('2 h');
  });
});

describe('Easier / Harder', () => {
  it('re-steps the zones 2% a step, from the runner\'s basis race', () => {
    const harder = built('5x1km at threshold, 2 min easy', 1);
    const z1 = pacesFor(basis, 1).zones;
    const rep = harder.detail.steps[1];
    expect(rep.kind === 'repeat' && rep.steps[0].target).toEqual({ kind: 'pace', zone: 'threshold', range: z1.threshold });
    expect(z1.threshold[0]).toBeLessThan(pacesFor(basis, 0).zones.threshold[0]);
    expect(adjustLine(basis, 'threshold', 1)).toBe('Threshold from your 10K of 43:30 · Harder by 2%');
    expect(adjustLine(basis, 'threshold', -2)).toBe('Threshold from your 10K of 43:30 · Easier by 4%');
  });
  it('a pace written out stays as written', () => {
    expect(built('6x800m at 3:50/km 400m jog', 2).targets).toBe('3:50 /km');
    expect(built('6x800m at 3:50/km 400m jog', 2).detail.adjustable).toBe(false);
  });
  it('before a watch is connected: a 10K of 45:00, and it says so', () => {
    const b = build(parseWorkout('5x1km at threshold'), { basis: null, step: 0, date: '2026-12-04' })!;
    expect(b.targets).toBe(`${pacesFor(null, 0).zones.threshold.map((s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`).join('–')} /km`);
    expect(adjustLine(null, 'threshold', 0)).toBe('Threshold from a 10K of 45:00 until a watch is connected');
  });
});

describe('daash.workout.v1', () => {
  it('the design\'s session, exactly', () => {
    const z = pacesFor(basis, 0).zones;
    const p = (r: [number, number]) => r.map((s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`);
    expect(built('5x1km at threshold, 2 min easy').workout).toEqual({
      schema: 'daash.workout.v1', name: '5 × 1 km at threshold', sport: 'running',
      description: '5 repeats · 1 km at threshold · 2 min easy jog',
      steps: [
        { type: 'warmup', duration_seconds: 600, pace: p(z.easy) },
        { type: 'repeat', times: 5, steps: [
          { type: 'interval', distance_meters: 1000, pace: p(z.threshold) },
          { type: 'recovery', duration_seconds: 120 },
        ] },
        { type: 'cooldown', duration_seconds: 600, pace: p(z.easy) },
      ],
    });
  });

  // The rules garmin_mcp/providers/workout.py normalise() enforces, over every session the corpus reads.
  const corpus = ['10 x 400m @ 5k pace w/ 200m jog', '4 x 1 mile at 10k pace, 90s jog', "3x10' at threshold, 2' jog", '8x30s strides',
    '8 km easy + 6 x 100 m strides', '6 x 400m at 5k, 90 s rest', '4 x 5 min at 160-165 bpm, 2 min walk', 'hr 150 for 40 min',
    'long run 28 km with last 8 at marathon', '16 km long run', '4x(1km at 10k, 400m jog)', '1km@4:10', '5 x 1000m'];
  it.each(corpus)('%s holds to the schema', (text) => {
    const w = built(text).workout;
    expect(w.schema).toBe('daash.workout.v1');
    expect(w.name.trim()).not.toBe('');
    const check = (s: WorkoutStep) => {
      expect(['warmup', 'interval', 'recovery', 'rest', 'cooldown']).toContain(s.type);
      const ends = ['duration_seconds', 'distance_meters'].filter((k) => k in s);
      expect(ends).toHaveLength(1);
      expect((s as unknown as Record<string, number>)[ends[0]]).toBeGreaterThan(0);
      expect(['pace', 'hr'].filter((k) => k in s).length).toBeLessThanOrEqual(1);
      if (s.pace) expect(s.pace[0] <= s.pace[1] || s.pace[0].length < s.pace[1].length).toBe(true);
    };
    for (const s of w.steps) {
      if (s.type === 'repeat') {
        expect(s.times).toBeGreaterThanOrEqual(1);
        expect(s.steps.length).toBeGreaterThan(0);
        s.steps.forEach((c) => { expect((c as { type: string }).type).not.toBe('repeat'); check(c); });
      } else check(s);
    }
  });
  it('a rest is a rest step, a walk a recovery with a note, strides an interval with no target', () => {
    const w = built('6 x 400m at 5k, 90 s rest').workout;
    expect(w.steps[1]).toMatchObject({ steps: [{}, { type: 'rest', duration_seconds: 90 }] });
    expect(built('4 x 5 min at 160-165 bpm, 2 min walk').workout.steps[1])
      .toMatchObject({ steps: [{ hr: [160, 165] }, { type: 'recovery', note: 'Walk' }] });
    expect(built('8x30s strides').workout.steps[0]).toEqual({ type: 'repeat', times: 8, steps: [{ type: 'interval', duration_seconds: 30, note: 'Strides' }] });
  });
});

describe('text from steps: the parser reads it back the same', () => {
  const texts = ['5x1km at threshold, 2 min easy', '10 x 400m @ 5k pace w/ 200m jog', "3x10' at threshold, 2' jog", '8 km easy + 6 x 100 m strides',
    '15 min warm-up, 3 x 10 min at tempo, 90 s easy, 10 min cool-down', 'long run 28 km with last 8 at marathon', '16 km long run',
    '6x800m at 3:50/km 400m jog', '4 x 5 min at 160-165 bpm, 2 min walk', '12 x 1:00 at vo2, 1:00 easy', 'hr 150 for 40 min', '1.5 km easy'];
  it.each(texts)('%s', (text) => {
    const first = parseWorkout(text);
    const again = parseWorkout(stepsText(first.steps));
    expect(again.problems).toEqual([]);
    expect(again.steps.map(stepWords)).toEqual(first.steps.map(stepWords));
  });

  it('a planned session\'s steps, for Edit', () => {
    const tempo = allSessions(PERSONA_PLAN).find((s) => s.title === '3 × 2 km at threshold')!;
    expect(sessionText(tempo.detail)).toBe('15 min warm-up, 3 x 2 km at threshold with 2 min jog, 10 min cool-down');
    const b = built(sessionText(tempo.detail)!);
    expect(b.title).toBe(tempo.title);
    expect(b.type).toBe(tempo.type);
  });
  it('strength isn\'t something the builder reads', () => {
    const strength = allSessions(PERSONA_PLAN).find((s) => s.type === 'strength' && s.detail)!;
    expect(sessionText(strength.detail)).toBeNull();
  });
});

describe('the day', () => {
  it('today and the next 13 days, with what\'s already planned', () => {
    const days = dayChoices(PERSONA_PLAN, '2026-11-27');
    expect(days).toHaveLength(14);
    expect(days[0]).toMatchObject({ date: '2026-11-27', label: 'Today', long: 'Fri 27 Nov' });
    expect(days[1]).toMatchObject({ label: 'Tomorrow' });
    expect(days[2].label).toBe('Sun 29');
    const planned = days.filter((d) => d.planned);
    expect(planned.length).toBeGreaterThan(0);
    for (const d of planned) expect(allSessions(PERSONA_PLAN).some((s) => s.date === d.date && s.title === d.planned)).toBe(true);
  });
  it('starts on the first day with nothing planned', () => {
    const days = dayChoices(PERSONA_PLAN, '2026-11-27');
    const first = firstFreeDay(days);
    expect(days.find((d) => d.date === first)!.planned).toBeNull();
    expect(days.filter((d) => d.date < first).every((d) => d.planned)).toBe(true);
  });
});

describe('Save and send: the watch confirm\'s line', () => {
  it('a new workout is one Add, carrying the workout', () => {
    const b = built('5x1km at threshold, 2 min easy');
    const p = buildProposal(b, { id: 'built-1', date: '2026-12-04' });
    expect(p.source).toBe('build');
    expect(changesPlan(p)).toBe(true);
    expect(p.subtitle).toBe('4:23–4:28 /km');
    expect(p.ops).toEqual([expect.objectContaining({ kind: 'add', sessionId: 'built-1', date: '2026-12-04', dayLabel: 'Fri 4',
      what: '5 × 1 km at threshold', withWhat: null, before: null, workout: b.workout })]);
  });
  it('editing a session replaces it on its day', () => {
    const tempo = allSessions(PERSONA_PLAN).find((s) => s.title === '3 × 2 km at threshold')!;
    const b = built('15 min warm-up, 4 x 2 km at threshold with 2 min jog, 10 min cool-down');
    const p = buildProposal(b, { id: 'x', date: tempo.date, replacing: tempo });
    expect(p.ops).toHaveLength(1);
    expect(p.ops[0]).toMatchObject({ kind: 'replace', sessionId: tempo.id, what: '3 × 2 km at threshold', withWhat: '4 × 2 km at threshold' });
  });
  it('editing a session onto another day takes it off the old one', () => {
    const tempo = allSessions(PERSONA_PLAN).find((s) => s.title === '3 × 2 km at threshold')!;
    const p = buildProposal(built('3 x 2 km at threshold'), { id: 'x', date: '2026-12-04', replacing: tempo });
    expect(p.ops.map((o) => [o.kind, o.date])).toEqual([['remove', tempo.date], ['add', '2026-12-04']]);
  });
  it('once confirmed, Plan shows it with its steps before the server has it', () => {
    const b = built('5x1km at threshold, 2 min easy');
    const p = buildProposal(b, { id: 'built-1', date: '2026-12-04' });
    const vm = applyToPlan(PERSONA_PLAN, [p], []);
    const s = findSession(vm, 'built-1');
    expect(s).toMatchObject({ title: '5 × 1 km at threshold', type: 'tempo', date: '2026-12-04', done: false });
    expect(s!.detail).toEqual(b.detail);
  });
});
