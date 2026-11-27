import { describe, expect, it } from '@jest/globals';
import { DEFAULT_FLAGS, parseFlags, tabsFor } from './flags';

describe('tabs', () => {
  it('are four with coach_enabled off, and nothing mentions a Coach', () => {
    expect(tabsFor({ coach_enabled: false })).toEqual(['today', 'plan', 'crew', 'you']);
  });
  it('are five with coach_enabled on, Coach after Plan', () => {
    expect(tabsFor({ coach_enabled: true })).toEqual(['today', 'plan', 'coach', 'crew', 'you']);
  });
});

describe('flags from the server', () => {
  it('default to coach off, free, garmin only, a limit of 10', () => {
    expect(DEFAULT_FLAGS.coach_enabled).toBe(false);
    expect(DEFAULT_FLAGS.plus).toBe(false);
    expect(DEFAULT_FLAGS.coach_daily_limit).toBe(10);
    expect(DEFAULT_FLAGS.providers).toEqual({ garmin: true });
  });
  it("read today's pilot answer and the v1 one alike", () => {
    expect(parseFlags({ chat: true }).coach_enabled).toBe(true);
    expect(parseFlags({ coach: true }).coach_enabled).toBe(true);
    expect(parseFlags({}).coach_enabled).toBe(false);
  });
  it('keep defaults for anything missing or malformed', () => {
    const f = parseFlags({ coach: 'yes', plus: true, readiness: { loadRatioHigh: 1.4, sleepShortHours: 'x' },
      merge: { startWithinSeconds: 90 }, providers: { garmin: true, coros: 'soon' } });
    expect(f.coach_enabled).toBe(false);
    expect(f.plus).toBe(true);
    expect(f.readiness.loadRatioHigh).toBe(1.4);
    expect(f.readiness.sleepShortHours).toBe(1);
    expect(f.merge).toEqual({ startWithinSeconds: 90, distanceWithinPct: 2 });
    expect(f.providers).toEqual({ garmin: true, coros: false });
    expect(parseFlags(null)).toBe(DEFAULT_FLAGS);
  });
});
