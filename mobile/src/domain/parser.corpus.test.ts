// The parser test corpus (M6 acceptance): how runners actually write sessions, from
// club WhatsApps, coach notes and training logs. Each one either reads exactly as
// below or is refused with a fix-it that names what to write instead. Nothing is
// guessed: "hard", "mile pace" and "parkrun" don't map to a zone without a number.
import { describe, expect, it } from '@jest/globals';

import { parseWorkout, stepWords } from './parser';

describe('the corpus: reads', () => {
  // [input, "Read as …", steps as words (added warm-up and cool-down included)]
  const reads: [string, string, string[]][] = [
    // repeats, the usual ways of writing them
    ['10 x 400m @ 5k pace w/ 200m jog', 'Read as 10 repeats · 400 m at 5K pace · 200 m jog',
      ['10 min warm-up (added)', '10 repeats · 400 m at 5K pace · 200 m jog', '10 min cool-down (added)']],
    ['4 x 1 mile at 10k pace, 90s jog', 'Read as 4 repeats · 1.61 km at 10K pace · 90 s jog',
      ['10 min warm-up (added)', '4 repeats · 1.61 km at 10K pace · 90 s jog', '10 min cool-down (added)']],
    ['3 × 2 km at threshold', 'Read as 3 repeats · 2 km at threshold',
      ['10 min warm-up (added)', '3 repeats · 2 km at threshold', '10 min cool-down (added)']],
    ['12 x 400 m at 5k, 200 m jog recovery', 'Read as 12 repeats · 400 m at 5K pace · 200 m jog',
      ['10 min warm-up (added)', '12 repeats · 400 m at 5K pace · 200 m jog', '10 min cool-down (added)']],
    ['6 x 3 min at vo2 max, 2 min jog', 'Read as 6 repeats · 3 min at VO2 pace · 2 min jog',
      ['10 min warm-up (added)', '6 repeats · 3 min at VO2 pace · 2 min jog', '10 min cool-down (added)']],
    ['5x1km @ HR 165-170', 'Read as 5 repeats · 1 km at 165–170 bpm',
      ['10 min warm-up (added)', '5 repeats · 1 km at 165–170 bpm', '10 min cool-down (added)']],
    ['5 x 1000m', 'Read as 5 repeats · 1 km', ['10 min warm-up (added)', '5 repeats · 1 km', '10 min cool-down (added)']],
    // the zone without "at", and the recovery in the same clause
    ['5x1km threshold 2min easy', 'Read as 5 repeats · 1 km at threshold · 2 min easy jog',
      ['10 min warm-up (added)', '5 repeats · 1 km at threshold · 2 min easy jog', '10 min cool-down (added)']],
    ['6x800m at 3:50/km 400m jog', 'Read as 6 repeats · 800 m at 3:50 /km · 400 m jog',
      ['10 min warm-up (added)', '6 repeats · 800 m at 3:50 /km · 400 m jog', '10 min cool-down (added)']],
    // coach shorthand: ' for minutes, " for seconds
    ["3x10' at threshold, 2' jog", 'Read as 3 repeats · 10 min at threshold · 2 min jog',
      ['10 min warm-up (added)', '3 repeats · 10 min at threshold · 2 min jog', '10 min cool-down (added)']],
    ['10 min wu, 4 x 5\' at 10k w/ 90" jog, 10 min cd', 'Read as 10 min warm-up · 4 repeats · 5 min at 10K pace · 90 s jog · 10 min cool-down',
      ['10 min warm-up', '4 repeats · 5 min at 10K pace · 90 s jog', '10 min cool-down']],
    // brackets
    ['4x(1km at 10k, 400m jog)', 'Read as 4 repeats · 1 km at 10K pace · 400 m jog',
      ['10 min warm-up (added)', '4 repeats · 1 km at 10K pace · 400 m jog', '10 min cool-down (added)']],
    // strides: no pace target, and not enough on their own to add a warm-up
    ['8x30s strides', 'Read as 8 repeats · 30 s strides', ['8 repeats · 30 s strides']],
    ['8 km easy + 6 x 100 m strides', 'Read as 8 km at easy · 6 repeats · 100 m strides', ['8 km at easy', '6 repeats · 100 m strides']],
    ['10 min easy, 6 x 20" strides, 10 min easy', 'Read as 10 min at easy · 6 repeats · 20 s strides · 10 min at easy',
      ['10 min at easy', '6 repeats · 20 s strides', '10 min at easy']],
    // steady blocks, either word order
    ['40 min easy', 'Read as 40 min at easy', ['40 min at easy']],
    ['1.5 km easy', 'Read as 1.5 km at easy', ['1.5 km at easy']],
    ['90 sec easy', 'Read as 90 s at easy', ['90 s at easy']],
    ['20 minutes tempo', 'Read as 20 min at tempo', ['10 min warm-up (added)', '20 min at tempo', '10 min cool-down (added)']],
    ['tempo 20 min', 'Read as 20 min at tempo', ['10 min warm-up (added)', '20 min at tempo', '10 min cool-down (added)']],
    ['jog 2 min', 'Read as 2 min jog', ['2 min jog']],
    ['5K at threshold', 'Read as 5 km at threshold', ['10 min warm-up (added)', '5 km at threshold', '10 min cool-down (added)']],
    ['30 min at marathon pace', 'Read as 30 min at marathon pace',
      ['10 min warm-up (added)', '30 min at marathon pace', '10 min cool-down (added)']],
    ['1km@4:10', 'Read as 1 km at 4:10 /km', ['10 min warm-up (added)', '1 km at 4:10 /km', '10 min cool-down (added)']],
    ['45 min at 140-150 bpm', 'Read as 45 min at 140–150 bpm',
      ['10 min warm-up (added)', '45 min at 140–150 bpm', '10 min cool-down (added)']],
    ['hr 150 for 40 min', 'Read as 40 min at 150 bpm', ['10 min warm-up (added)', '40 min at 150 bpm', '10 min cool-down (added)']],
    // long runs
    ['16 km long run', 'Read as 16 km long run at easy', ['16 km long run at easy']],
    ['long run 28 km with last 8 at marathon', 'Read as 20 km at easy · 8 km at marathon pace',
      ['20 km at easy', '8 km at marathon pace', '10 min cool-down (added)']],
    ['28 km with the last 8 km at marathon', 'Read as 20 km at easy · 8 km at marathon pace',
      ['20 km at easy', '8 km at marathon pace', '10 min cool-down (added)']],
    // capitals and "then"
    ['WU 2KM, 5 X 1KM AT THRESHOLD, 2 MIN JOG, CD 2KM', 'Read as 2 km warm-up · 5 repeats · 1 km at threshold · 2 min jog · 2 km cool-down',
      ['2 km warm-up', '5 repeats · 1 km at threshold · 2 min jog', '2 km cool-down']],
    ['15 min easy then 20 min at threshold then 10 min easy', 'Read as 15 min at easy · 20 min at threshold · 10 min at easy',
      ['15 min at easy', '20 min at threshold', '10 min at easy']],
  ];
  it.each(reads)('%s', (input, readAs, steps) => {
    const p = parseWorkout(input);
    expect(p.problems).toEqual([]);
    expect(p.ok).toBe(true);
    expect(p.readAs).toBe(readAs);
    expect(p.steps.map(stepWords)).toEqual(steps);
  });

  it('the long run keeps its label in the data, so the watch names it', () => {
    expect(parseWorkout('16 km long run').steps[0]).toMatchObject({ kind: 'run', distanceM: 16000, label: 'long',
      target: { kind: 'zone', zone: 'easy' } });
  });
  it('strides have no pace target', () => {
    const [rep] = parseWorkout('8x30s strides').steps;
    expect(rep).toMatchObject({ kind: 'repeat', times: 8, work: { durationS: 30, label: 'strides' } });
    expect(rep.kind === 'repeat' && rep.work.target).toBeUndefined();
  });
  it('brackets keep the right spans for a problem inside them', () => {
    const input = '4x(1km at 10kk, 400m jog)';
    const [problem] = parseWorkout(input).problems;
    expect(input.slice(problem.start, problem.end)).toBe('10kk');
    expect(problem.suggestion).toBe('Did you mean "10k"?');
  });
});

describe('the corpus: refused, with what to write instead', () => {
  // [input, the highlighted text, the suggestion]
  const refusals: [string, string, string][] = [
    // effort words with no number behind them
    ['6 x 400m hard', 'hard', 'For a hard effort name the zone (threshold, 10K, 5K or VO2) or a pace like 4:00/km'],
    ['10 x 1 min fast', 'fast', 'For a hard effort name the zone (threshold, 10K, 5K or VO2) or a pace like 4:00/km'],
    ['5x1km gnarly', 'gnarly', 'Use a zone (easy, steady, marathon, threshold, tempo, 10K, 5K, VO2), a pace like 4:05/km or a heart rate like 150 bpm'],
    // paces Daash has no zone for
    ['6 x 1 km at mile pace', 'mile pace', '5K pace is the fastest zone; or give a pace like 3:45/km'],
    ['5km parkrun', 'parkrun', 'For a race effort give its pace, like "5 km at 5K pace"'],
    ['fartlek 30 min', 'fartlek', 'Write the pieces, like "10 x 1 min at 5K pace, 1 min easy"'],
    // something missing
    ['easy run', 'easy run', 'Give it a time or distance, like "40 min easy" or "8 km easy"'],
    ['10x400 with 200 jog', '200', 'Did you mean "200 m"?'],
    ['5x1kn at threshold', '1kn', 'Did you mean "1 km"?'],
    // not yet
    ['2 x (3 x 1km at 10k, 1 min jog)', '2 x (3 x 1km at 10k, 1 min jog)', 'Write each set out in turn, with the rest between sets'],
  ];
  it.each(refusals)('%s', (input, text, suggestion) => {
    const p = parseWorkout(input);
    expect(p.ok).toBe(false);
    expect(p.problems).toHaveLength(1);
    const [problem] = p.problems;
    expect(input.slice(problem.start, problem.end)).toBe(text);
    expect(problem.suggestion).toBe(suggestion);
  });

  it('a refusal never leaves a half-read session behind it', () => {
    // The steps that did read stay, so the builder can show them, but nothing is added
    // around them and the session can't be saved.
    const p = parseWorkout('10 min warm-up, 6 x 400m hard, 10 min cool-down');
    expect(p.ok).toBe(false);
    expect(p.steps.map(stepWords)).toEqual(['10 min warm-up', '10 min cool-down']);
  });
});
