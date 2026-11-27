import { describe, expect, it } from '@jest/globals';

import { parseWorkout, stepWords } from './parser';

const read = (text: string) => parseWorkout(text);

describe('text to workout: the corpus', () => {
  // [input, "Read as …", steps as words (added warm-up and cool-down included)]
  const corpus: [string, string, string[]][] = [
    ['5x1km at threshold, 2 min easy', 'Read as 5 repeats · 1 km at threshold · 2 min easy jog',
      ['10 min warm-up (added)', '5 repeats · 1 km at threshold · 2 min easy jog', '10 min cool-down (added)']],
    ['5 x 1 km @ threshold with 2 min jog', 'Read as 5 repeats · 1 km at threshold · 2 min jog',
      ['10 min warm-up (added)', '5 repeats · 1 km at threshold · 2 min jog', '10 min cool-down (added)']],
    ['6×800m at 5k, 400m jog', 'Read as 6 repeats · 800 m at 5K pace · 400 m jog',
      ['10 min warm-up (added)', '6 repeats · 800 m at 5K pace · 400 m jog', '10 min cool-down (added)']],
    ['15 min warm-up, 3 x 10 min at tempo, 2 min easy, 10 min cool-down',
      'Read as 15 min warm-up · 3 repeats · 10 min at tempo · 2 min easy jog · 10 min cool-down',
      ['15 min warm-up', '3 repeats · 10 min at tempo · 2 min easy jog', '10 min cool-down']],
    ['warm up 2km then 20 min at 4:20/km then cool down 2km',
      'Read as 2 km warm-up · 20 min at 4:20 /km · 2 km cool-down', ['2 km warm-up', '20 min at 4:20 /km', '2 km cool-down']],
    ['10 x 400m at 1:28-1:32/km, 90 s rest', 'Read as 10 repeats · 400 m at 1:28–1:32 /km · 90 s rest',
      ['10 min warm-up (added)', '10 repeats · 400 m at 1:28–1:32 /km · 90 s rest', '10 min cool-down (added)']],
    ['8 km easy', 'Read as 8 km at easy', ['8 km at easy']],
    ['45 min easy', 'Read as 45 min at easy', ['45 min at easy']],
    ['10 mi at marathon pace', 'Read as 16.09 km at marathon pace',
      ['10 min warm-up (added)', '16.09 km at marathon pace', '10 min cool-down (added)']],
    ['4 x 5 min at 160-165 bpm, 2 min walk', 'Read as 4 repeats · 5 min at 160–165 bpm · 2 min walk',
      ['10 min warm-up (added)', '4 repeats · 5 min at 160–165 bpm · 2 min walk', '10 min cool-down (added)']],
    ['3x2mi at 6:40/mi, 3 min jog', 'Read as 3 repeats · 3.22 km at 4:09 /km · 3 min jog',
      ['10 min warm-up (added)', '3 repeats · 3.22 km at 4:09 /km · 3 min jog', '10 min cool-down (added)']],
    ['12 x 1:00 at vo2, 1:00 easy', 'Read as 12 repeats · 1 min at VO2 pace · 1 min easy jog',
      ['10 min warm-up (added)', '12 repeats · 1 min at VO2 pace · 1 min easy jog', '10 min cool-down (added)']],
    ['wu, 5x1k at 10K, 2 min easy, cd', 'Read as 10 min warm-up · 5 repeats · 1 km at 10K pace · 2 min easy jog · 10 min cool-down',
      ['10 min warm-up', '5 repeats · 1 km at 10K pace · 2 min easy jog', '10 min cool-down']],
    ['30 min steady', 'Read as 30 min at steady', ['10 min warm-up (added)', '30 min at steady', '10 min cool-down (added)']],
    ['2 x 20 min at threshold pace, 5 min easy', 'Read as 2 repeats · 20 min at threshold · 5 min easy jog',
      ['10 min warm-up (added)', '2 repeats · 20 min at threshold · 5 min easy jog', '10 min cool-down (added)']],
    ['20 min easy + 6 x 20 s at 5k + 10 min easy', 'Read as 20 min at easy · 6 repeats · 20 s at 5K pace · 10 min at easy',
      ['20 min at easy', '6 repeats · 20 s at 5K pace', '10 min at easy']],
  ];
  it.each(corpus)('%s', (input, readAs, steps) => {
    const p = read(input);
    expect(p.problems).toEqual([]);
    expect(p.ok).toBe(true);
    expect(p.readAs).toBe(readAs);
    expect(p.steps.map(stepWords)).toEqual(steps);
  });
});

describe('text to workout: what it holds as data', () => {
  it('distances in metres, times in seconds, paces in seconds per km', () => {
    const p = read('5x1km at 4:05/km, 2 min easy');
    const rep = p.steps[1];
    expect(rep).toMatchObject({ kind: 'repeat', times: 5, work: { distanceM: 1000, target: { kind: 'pace', fast: 245, slow: 245 } },
      recovery: { durationS: 120, how: 'easy' } });
  });
  it('marks only the warm-up and cool-down it added', () => {
    const p = read('10 min warm-up, 4 x 1 km at threshold, 2 min jog');
    expect(p.steps[0]).toMatchObject({ kind: 'warmup', added: false });
    expect(p.steps[p.steps.length - 1]).toMatchObject({ kind: 'cooldown', added: true });
  });
  it('adds nothing to an easy run', () => {
    expect(read('8 km easy').steps).toHaveLength(1);
  });
});

describe('text to workout: never guesses silently', () => {
  it('a misspelt zone is highlighted with the likely word, not corrected', () => {
    const input = '5x1km at thresold, 2 min easy';
    const p = read(input);
    expect(p.ok).toBe(false);
    expect(p.problems).toHaveLength(1);
    const [problem] = p.problems;
    expect(input.slice(problem.start, problem.end)).toBe('thresold');
    expect(problem.suggestion).toBe('Did you mean "threshold"?');
    expect(p.steps.some((s) => s.kind === 'repeat')).toBe(false);
  });
  it('a number without a unit asks for one', () => {
    const p = read('5 x 1 at threshold');
    expect(p.problems[0].message).toBe('Add a unit: 1 km, 400 m, 3 min or 90 s');
  });
  it('an unknown target lists what works', () => {
    const p = read('20 min at whatever');
    expect(p.problems[0].text).toBe('whatever');
    expect(p.problems[0].suggestion).toMatch(/^Use a zone/);
  });
  it('a clause it can\'t read at all is highlighted whole, the rest still reads', () => {
    const input = '10 min warm-up, run like the wind, 10 min cool-down';
    const p = read(input);
    expect(p.problems).toHaveLength(1);
    expect(input.slice(p.problems[0].start, p.problems[0].end)).toBe('run like the wind');
    expect(p.steps.map(stepWords)).toEqual(['10 min warm-up', '10 min cool-down']);
    expect(p.ok).toBe(false);
  });
  it('nothing at all is not ok', () => {
    expect(read('   ').ok).toBe(false);
  });
});
