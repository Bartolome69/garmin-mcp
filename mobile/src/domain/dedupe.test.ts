import { describe, expect, it } from '@jest/globals';

import FIXTURE from './dedupe.fixture.json';
import { merge, sameRun, type Activity } from './dedupe';

const F = (FIXTURE as unknown as { cases: { activities: Activity[]; cases: { priority: string[]; keptApart: [string, string][]; shown: string[]; merges: [string, string][] }[] } }).cases;
const byId = new Map(F.activities.map((a) => [a.id, a]));

type Case = (typeof F.cases)[number];
describe('de-duplication: the server\'s answers, exactly', () => {
  it.each(F.cases.map((c): [string, Case] => [`${c.priority.join(' > ') || 'no order'}${c.keptApart.length ? ', one pair kept apart' : ''}`, c]))('%s', (_, c) => {
    const got = merge(F.activities, c.priority, c.keptApart);
    expect(got.shown.map((a) => a.id)).toEqual(c.shown);
    expect(got.merges.map((m) => [m.kept, m.hidden])).toEqual(c.merges);
  });
});

describe('de-duplication: the rule', () => {
  it('60 s and 2% are inside; 61 s or 2.1% are not; one source never merges with itself', () => {
    expect(sameRun(byId.get('e-a')!, byId.get('e-b')!)).toBe(true);
    expect(sameRun(byId.get('e-c')!, byId.get('e-d')!)).toBe(false);
    expect(sameRun(byId.get('e-e')!, byId.get('e-f')!)).toBe(false);
    expect(merge([byId.get('e-g')!, byId.get('e-h')!], ['garmin']).merges).toEqual([]);
  });
  it('starts are instants: a London run and its UTC copy an hour "earlier" on the clock are one', () => {
    expect(sameRun(byId.get('e-i')!, byId.get('e-j')!)).toBe(true);
  });
  it('hidden, never deleted: every activity is either shown or in a merge', () => {
    const { shown, merges } = merge(F.activities, ['garmin', 'health', 'intervals']);
    expect(shown.length + merges.length).toBe(F.activities.length);
  });
});
