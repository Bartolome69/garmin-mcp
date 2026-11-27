// Moving a session (milestone 4): the drop check, the warning words, the proposal.
import { describe, expect, it } from '@jest/globals';

import { PERSONA_PLAN } from './fixture';
import { allSessions, findSession } from './model';
import { clashWords, drop, moveTargets, proposalWords, withProposal } from './moves';

const TEMPO = '2026-11-27-tempo';
const INTERVALS = '2026-12-02-intervals';

describe('drop', () => {
  it('a move with no clash becomes a proposal', () => {
    expect(drop(PERSONA_PLAN, TEMPO, '2026-11-28')).toEqual({ kind: 'propose', proposal: { id: TEMPO, from: '2026-11-27', to: '2026-11-28' } });
  });

  it('two hard days back to back warn first, naming both and the day it came from', () => {
    const r = drop(PERSONA_PLAN, TEMPO, '2026-12-03');
    expect(r.kind).toBe('warn');
    if (r.kind !== 'warn') return;
    expect(r.title).toBe('Back-to-back hard days.');
    expect(r.words).toBe('Intervals on Wed and tempo on Thu leave no easy day between.');
    expect(r.keepLabel).toBe('Keep it on Fri');
    expect(r.proposal).toEqual({ id: TEMPO, from: '2026-11-27', to: '2026-12-03' });
  });

  it('a clash already in the plan does not warn again', () => {
    const vm = withProposal(PERSONA_PLAN, { id: TEMPO, from: '2026-11-27', to: '2026-12-03' });
    expect(drop(vm, INTERVALS, '2026-12-01').kind).toBe('propose');
  });

  it('dropping on its own day does nothing', () => {
    expect(drop(PERSONA_PLAN, TEMPO, '2026-11-27')).toEqual({ kind: 'none' });
  });

  it('a run already done, a rest day and the past stay put', () => {
    expect(drop(PERSONA_PLAN, '2026-11-25-intervals', '2026-11-28')).toEqual({ kind: 'refused', message: 'A run you’ve done stays on its day.' });
    expect(drop(PERSONA_PLAN, '2026-11-30-rest', '2026-12-01').kind).toBe('refused');
    expect(drop(PERSONA_PLAN, TEMPO, '2026-11-26')).toEqual({ kind: 'refused', message: 'Sessions can’t move into the past.' });
  });
});

describe('the proposal', () => {
  it('draws the session on its new day, waiting for the confirm, and changes nothing else', () => {
    const vm = withProposal(PERSONA_PLAN, { id: TEMPO, from: '2026-11-27', to: '2026-11-28' });
    const moved = findSession(vm, TEMPO)!;
    expect(moved.date).toBe('2026-11-28');
    expect(moved.watch).toEqual({ state: 'confirming', at: null });
    const fri = vm.weeks[1].days.find((d) => d.date === '2026-11-27')!;
    const sat = vm.weeks[1].days.find((d) => d.date === '2026-11-28')!;
    expect(fri.sessions).toEqual([]);
    expect(sat.sessions.map((s) => s.id)).toEqual(['2026-11-28-strength', TEMPO]);
    expect(allSessions(vm)).toHaveLength(allSessions(PERSONA_PLAN).length);
    expect(PERSONA_PLAN.weeks[1].days[4].sessions[0].id).toBe(TEMPO); // the server's copy is untouched
  });

  it('a move onto a planned rest day replaces the rest', () => {
    const vm = withProposal(PERSONA_PLAN, { id: INTERVALS, from: '2026-12-02', to: '2026-12-04' });
    expect(vm.weeks[2].days[4].sessions.map((s) => s.id)).toEqual([INTERVALS]);
  });

  it('says what is moving', () => {
    expect(proposalWords(PERSONA_PLAN, { id: TEMPO, from: '2026-11-27', to: '2026-11-28' })).toBe('Moving tempo from Fri to Sat.');
  });

  it('offers the days of the week that are still ahead', () => {
    const t = moveTargets(PERSONA_PLAN, TEMPO);
    expect(t.map((x) => x.label)).toEqual(['Mon 23', 'Tue 24', 'Wed 25', 'Thu 26', 'Fri 27', 'Sat 28', 'Sun 29']);
    expect(t.filter((x) => !x.disabled).map((x) => x.date)).toEqual(['2026-11-28', '2026-11-29']);
  });
});

it('clash words name the long run plainly', () => {
  const s = (id: string, date: string, type: string, distance_km?: number) => ({ id, date, type, distance_km });
  expect(clashWords({ first: s('a', '2026-12-05', 'tempo'), second: s('b', '2026-12-06', 'long', 30), message: '' }))
    .toBe('Tempo on Sat and the long run on Sun leave no easy day between.');
});
