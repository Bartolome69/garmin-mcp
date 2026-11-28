// The Coach's reply guard, on the phone (build prompt, sections 1, 5.10 and 12): the
// twin of garmin_mcp/domain/coach_guard.py. The server checks every reply before it
// sends it (TODO(server): wired into chat_pilot.py on main); the app runs the same
// check on what it receives as a second line, and marks a reply that fails. It never
// rewrites a reply or its numbers.
//
// The rules aren't written here: they're read from the corpus file the Python guard
// writes (coach_replies.fixture.json, by scripts/domain_fixtures.py), so the two
// sides can't drift. Pure: no React, no network.
import FIXTURE from './coach_replies.fixture.json';

export type GuardKind = 'cause' | 'medical' | 'diagnosis' | 'certainty';
export const GUARD_KINDS: GuardKind[] = ['cause', 'medical', 'diagnosis', 'certainty'];
export type Finding = { kind: GuardKind; match: string; at: number };

export const RULES = (FIXTURE as unknown as { cases: { rules: Record<GuardKind, string[]> } }).cases.rules;
const COMPILED = GUARD_KINDS.map((kind) => ({ kind, rx: RULES[kind].map((p) => new RegExp(p, 'gi')) }));

const plain = (text: string) => (text ?? '').replace(/[‘’]/g, "'");

/** Every finding in a reply, in reading order. */
export function checkReply(text: string): Finding[] {
  const s = plain(text);
  const found: Finding[] = [];
  for (const { kind, rx } of COMPILED) {
    for (const r of rx) {
      r.lastIndex = 0;
      for (let m = r.exec(s); m; m = r.exec(s)) {
        found.push({ kind, match: m[0], at: m.index });
        if (m[0] === '') r.lastIndex += 1;
      }
    }
  }
  return found.sort((a, b) => a.at - b.at || GUARD_KINDS.indexOf(a.kind) - GUARD_KINDS.indexOf(b.kind));
}

/** The kinds a reply breaks, each once, in GUARD_KINDS order. Empty: it passes. */
export function guardKinds(text: string): GuardKind[] {
  const seen = new Set(checkReply(text).map((f) => f.kind));
  return GUARD_KINDS.filter((k) => seen.has(k));
}

// -- numbers ---------------------------------------------------------------------------

const norm = (t: string) => t.replace(/^0+(?=\d)/, '').replace(/\.0+$/, '');

/** The numbers written in a text, normalised (no leading zero, no trailing .0), in order. */
export function numbersIn(text: string): string[] {
  return (plain(text).match(/\d+(?:[.:]\d+)*/g) ?? []).map(norm);
}

function payloadNumbers(value: unknown, out: Set<string>): void {
  if (value == null || typeof value === 'boolean') return;
  if (typeof value === 'number') out.add(norm(String(value)));
  else if (typeof value === 'string') numbersIn(value).forEach((n) => out.add(n));
  else if (Array.isArray(value)) value.forEach((v) => payloadNumbers(v, out));
  else if (typeof value === 'object') Object.values(value as Record<string, unknown>).forEach((v) => payloadNumbers(v, out));
}

/** Every number anywhere in a payload, as numbersIn writes them. */
export function numbersOfPayload(payload: unknown): Set<string> {
  const out = new Set<string>();
  payloadNumbers(payload, out);
  return out;
}

/** Numbers in the text found nowhere in the payload, each once. */
export function unmatchedNumbers(text: string, payload: unknown): string[] {
  const known = numbersOfPayload(payload);
  return [...new Set(numbersIn(text).filter((n) => !known.has(n)))];
}
