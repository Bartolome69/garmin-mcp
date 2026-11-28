/// <reference types="node" />
/**
 * @jest-environment node
 */
// The phone's reply guard answers exactly as the server's: every reply in the corpus
// (garmin_mcp/domain/fixtures/coach_replies.json, labelled by hand and checked by the
// Python guard) gets the same kinds here, and every number case the same numbers.
import { describe, expect, it } from '@jest/globals';
import { readFileSync } from 'fs';
import { join } from 'path';

import { checkReply, guardKinds, numbersIn, RULES, unmatchedNumbers } from './replyGuard';
import COPY from './replies.fixture.json';

type Corpus = {
  rules: Record<string, string[]>;
  replies: { text: string; label: string[]; kinds: string[] }[];
  numbers: { text: string; payload: unknown; label: string[]; unmatched: string[] }[];
};
const corpus = JSON.parse(readFileSync(join(__dirname, '..', '..', '..', 'garmin_mcp', 'domain', 'fixtures', 'coach_replies.json'), 'utf8')).cases as Corpus;

describe('the Coach reply corpus', () => {
  it('the phone reads the server’s rules (its copy is the server’s file)', () => {
    expect(RULES).toEqual(corpus.rules);
    expect((COPY as unknown as { cases: Corpus }).cases).toEqual(corpus);
  });
  it(`all ${corpus.replies.length} replies get the server’s kinds, and those are the hand labels`, () => {
    expect(corpus.replies.filter((r) => r.label.length).length).toBeGreaterThanOrEqual(20);
    expect(corpus.replies.filter((r) => !r.label.length).length).toBeGreaterThanOrEqual(15);
    for (const r of corpus.replies) {
      expect({ text: r.text, kinds: guardKinds(r.text) }).toEqual({ text: r.text, kinds: r.kinds });
      expect(r.kinds).toEqual(r.label);
    }
  });
  it('observations pass; causes, medical advice, diagnoses and certainty are caught', () => {
    expect(guardKinds('Rep 6 peaked at 172 bpm, 4 above rep 5.')).toEqual([]);
    expect(guardKinds('Because your HRV dropped, you are more tired than usual.')).toEqual(['cause']);
    expect(guardKinds('In line with your short sleep.')).toEqual(['cause']);
    expect(guardKinds('Take ibuprofen.')).toEqual(['medical']);
    expect(guardKinds('Sounds like shin splints.')).toEqual(['diagnosis']);
    expect(guardKinds('You must run today.')).toEqual(['certainty']);
    expect(guardKinds("Daash doesn’t give medical advice. If you’re unwell or hurt, check with a doctor or physio.")).toEqual([]);
  });
  it('findings say what matched and where, in reading order', () => {
    expect(checkReply('Because your HRV dropped, you are tired. You must rest.').map((f) => [f.kind, f.at, f.match]))
      .toEqual([['cause', 0, 'Because your HRV'], ['certainty', 41, 'You must']]);
  });
  it(`all ${corpus.numbers.length} number cases match the server`, () => {
    for (const n of corpus.numbers) {
      expect({ text: n.text, unmatched: unmatchedNumbers(n.text, n.payload) }).toEqual({ text: n.text, unmatched: n.unmatched });
      expect(n.unmatched).toEqual(n.label);
    }
    expect(numbersIn('Synced 07:02, 6.0 km, 4:01 /km')).toEqual(['7:02', '6', '4:01']);
  });
});
