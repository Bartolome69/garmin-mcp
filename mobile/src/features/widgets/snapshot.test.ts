import { describe, expect, it } from '@jest/globals';

import { PERSONA_PLAN } from '../plan/fixture';
import { PERSONA_TODAY } from '../today/fixture';
import type { TodayVM } from '../today/model';
import { EMPTY_MESSAGE, LOCKED_MESSAGE, LOCKED_USED_MESSAGE, buildSnapshot, sameDrawing, updatedLabel, widgetWatchWords } from './snapshot';
import { FakeWidgetBridge, NativeWidgetBridge, SNAPSHOT_KEY } from './bridge';

// Local times: the persona's Friday 27 Nov, read at 07:02.
const READ = new Date(2026, 10, 27, 7, 2).getTime();
const MIN = 60000;
const today = { data: PERSONA_TODAY, fetchedAt: READ };

describe('widget snapshot (the P1-Widgets boards)', () => {
  it('small: readiness ring, decision, today’s session and the watch', () => {
    const s = buildSnapshot({ today, block: PERSONA_PLAN.block, plus: true, now: READ + 5 * MIN });
    expect(s.locked).toBe(false);
    expect(s.message).toBeNull();
    expect(s.updated).toBeNull();
    expect(s.readiness).toEqual({ score: 26, word: 'Back off', decision: 'back_off' });
    expect(s.session).toMatchObject({ line: 'Today · Tempo', title: '3 × 2 km', watch: 'on watch', type: 'tempo' });
  });

  it('countdown, week strip and lock screen read as on the board', () => {
    const s = buildSnapshot({ today, block: PERSONA_PLAN.block, plus: true, now: READ + 5 * MIN });
    expect(s.countdown).toEqual({ race: 'London', days: 148, date: 'Sat 24 Apr', raceDay: '2027-04-24' });
    expect(s.week?.title).toBe('Week 4 of 21 · Base');
    expect(s.week?.summary).toBe('This week · 28 of 54 km');
    expect(s.week?.days.map((d) => `${d.letter}${d.day} ${d.label}`)).toEqual(
      ['M23 Rest', 'T24 Easy', 'W25 Int', 'T26 Easy', 'F27 Tempo', 'S28 Str', 'S29 Long']);
    expect(s.week?.days.filter((d) => d.today).map((d) => d.day)).toEqual([27]);
    expect(s.lock).toEqual({
      circular: { score: 26, word: 'Back off' },
      rectangular: { line1: 'Back off · Tempo 3 × 2 km', line2: 'On your watch · 10 km' },
    });
  });

  it('without the plan cached, the week title is the week alone', () => {
    expect(buildSnapshot({ today, plus: true, now: READ }).week?.title).toBe('Week 4 of 21');
  });

  it('old data says when it’s from, never a blank', () => {
    expect(buildSnapshot({ today, plus: true, now: READ + 29 * MIN }).updated).toBeNull();
    expect(buildSnapshot({ today, plus: true, now: READ + 3 * 60 * MIN }).updated).toBe('Updated 07:02');
    const nextDay = buildSnapshot({ today, plus: true, now: new Date(2026, 10, 28, 9, 0).getTime() });
    expect(nextDay.updated).toBe('Updated yesterday');
    // Yesterday's session is named, and the countdown keeps counting.
    expect(nextDay.session?.line).toBe('Yesterday · Tempo');
    expect(nextDay.countdown?.days).toBe(147);
    expect(nextDay.week?.days.filter((d) => d.today).map((d) => d.day)).toEqual([28]);
    const later = buildSnapshot({ today, plus: true, now: new Date(2026, 10, 30, 9, 0).getTime() });
    expect(later.updated).toBe('Updated 27 Nov');
    expect(later.session?.line).toBe('Fri · Tempo');
    expect(later.readiness?.word).toBe('Back off');
  });

  it('Free: the Plus message and no data at all', () => {
    const s = buildSnapshot({ today, plus: false, now: READ });
    expect(s).toMatchObject({ locked: true, message: LOCKED_MESSAGE, readiness: null, session: null, countdown: null, week: null, dataAt: null });
    expect(s.lock).toEqual({ circular: null, rectangular: null });
  });

  it('Free after the free week was used: no "try it free" the store won’t give', () => {
    expect(buildSnapshot({ today, plus: false, trialUsed: true, now: READ }).message).toBe(LOCKED_USED_MESSAGE);
    expect(LOCKED_USED_MESSAGE).not.toMatch(/free/i);
    // Plus wins over an old free week.
    expect(buildSnapshot({ today, plus: true, trialUsed: true, now: READ }).message).toBeNull();
  });

  it('nothing cached yet: says to open Daash', () => {
    expect(buildSnapshot({ today: null, plus: true, now: READ })).toMatchObject({ locked: false, message: EMPTY_MESSAGE, readiness: null });
  });

  it('a rest day and no readiness decision still draw something', () => {
    const rest: TodayVM = { ...PERSONA_TODAY, session: null, readiness: { ...PERSONA_TODAY.readiness, decision: null, word: null, garminScore: null } };
    const s = buildSnapshot({ today: { data: rest, fetchedAt: READ }, plus: true, now: READ });
    expect(s.readiness).toBeNull();
    expect(s.session).toBeNull();
    expect(s.lock).toEqual({ circular: null, rectangular: null });
    expect(s.countdown?.race).toBe('London');
    const restWithReadiness: TodayVM = { ...PERSONA_TODAY, session: null };
    expect(buildSnapshot({ today: { data: restWithReadiness, fetchedAt: READ }, plus: true, now: READ }).lock.rectangular).toEqual({ line1: 'Back off', line2: 'Rest day' });
  });

  it('the watch is always words', () => {
    expect(['on_watch', 'changed_pending_sync', 'sending', 'failed', 'not_sent', 'confirming'].map((s) => widgetWatchWords(s as never))).toEqual(
      ['on watch', 'changes at next sync', 'sending', 'not sent', 'not on watch', 'not on watch']);
    const failed: TodayVM = { ...PERSONA_TODAY, session: { ...PERSONA_TODAY.session!, watch: { state: 'failed', at: null } } };
    expect(buildSnapshot({ today: { data: failed, fetchedAt: READ }, plus: true, now: READ }).lock.rectangular?.line2).toBe('Not sent · 10 km');
  });

  it('a snapshot that draws the same isn’t rewritten', () => {
    const a = buildSnapshot({ today, plus: true, now: READ });
    expect(sameDrawing(a, buildSnapshot({ today, plus: true, now: READ + MIN }))).toBe(true);
    expect(sameDrawing(a, buildSnapshot({ today, plus: false, now: READ }))).toBe(false);
    expect(sameDrawing(null, a)).toBe(false);
  });

  it('updated labels', () => {
    expect(updatedLabel(null, READ)).toBeNull();
    expect(updatedLabel(READ, READ + 31 * MIN)).toBe('Updated 07:02');
  });
});

describe('widget bridge', () => {
  it('the fake keeps what was written; the native one writes JSON under the shared key', async () => {
    const fake = new FakeWidgetBridge();
    const s = buildSnapshot({ today, plus: true, now: READ });
    await fake.write(s);
    await fake.reload();
    expect(fake.last).toBe(s);
    expect(fake.reloads).toBe(1);
    const calls: [string, string][] = [];
    const native = new NativeWidgetBridge({ write: async (k, j) => { calls.push([k, j]); }, reload: async () => {} });
    await native.write(s);
    expect(calls[0][0]).toBe(SNAPSHOT_KEY);
    expect(JSON.parse(calls[0][1])).toEqual(s);
  });
});
