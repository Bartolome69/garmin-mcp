/// <reference types="node" />
/**
 * @jest-environment node
 */
// The app's twins answer exactly as the server's engines do: every case in
// garmin_mcp/domain/fixtures/ was written by the Python engines
// (scripts/domain_fixtures.py), and must come out the same here.
import { describe, expect, it } from '@jest/globals';
import { readFileSync } from 'fs';
import { join } from 'path';

import { checkMove, clashes, type PlanSession } from './backToBack';
import { defaultPaces, paces, paceRangeWords } from './paces';

const fixture = <T,>(name: string): T[] =>
  JSON.parse(readFileSync(join(__dirname, '..', '..', '..', 'garmin_mcp', 'domain', 'fixtures', name), 'utf8')).cases;

type PaceCase = { race_km: number; race_seconds: number; race_date: string | null; step: number;
  zones: Record<string, [number, number]>; basis: string };
type ClashCase = { plan: string; sessions: PlanSession[]; move: { id: string; to: string } | null; clashes: string[] };

describe('paces match the server', () => {
  const cases = fixture<PaceCase>('paces.json');
  it(`all ${cases.length} cases`, () => {
    expect(cases.length).toBeGreaterThan(30);
    for (const c of cases) {
      const p = paces(c.race_km, c.race_seconds, { raceDate: c.race_date, step: c.step });
      expect({ basis: p.basis, zones: p.zones }).toEqual({ basis: c.basis, zones: c.zones });
    }
  });
  it('defaults to a 10K of 45:00 before a watch is connected', () => {
    expect(defaultPaces().basis).toBe('Based on your 10K of 45:00');
    expect(paceRangeWords(defaultPaces().zones['10k'])).toBe('4:27–4:33 /km');
  });
});

describe('back-to-back matches the server', () => {
  const cases = fixture<ClashCase>('backtoback.json');
  it(`all ${cases.length} cases`, () => {
    expect(cases.filter((c) => c.clashes.length).length).toBeGreaterThan(10);
    for (const c of cases) {
      const got = c.move ? checkMove(c.sessions, c.move.id, c.move.to) : clashes(c.sessions);
      expect({ plan: c.plan, move: c.move, clashes: got.map((x) => x.message) })
        .toEqual({ plan: c.plan, move: c.move, clashes: c.clashes });
    }
  });
});
