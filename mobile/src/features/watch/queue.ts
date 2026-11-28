// The watch operation queue's state machine (build prompt, sections 5.8 and 7).
// Pure: no React, no network, no clock of its own.
//
//   confirming (the sheet) → sending → changed_pending_sync → on_watch
//                                   ↘ failed ─(backoff or Resend)→ sending
//
// Garmin accepting a change isn't the watch having it: an accepted change reads
// "Changed · arrives at next sync" until the watch syncs, then "On your watch"
// (docs/native-app/DECISIONS.md, section 0 default). A failure that may pass
// (Garmin didn't answer, no connection) retries on its own with exponential
// backoff; one Garmin refused waits for Resend. The screen always shows the
// current state, and every write ends in one you can see.

import type { Watch } from '../plan/model';
import type { WatchState } from '../today/model';
import type { OpDraft } from './model';

export type WatchOp = OpDraft & {
  id: string;
  batch: string;                 // the confirm it came from
  state: WatchState;
  attempts: number;
  nextAttemptAt: number | null;  // a failed op's next automatic try; null: none planned
  error: string | null;          // in words: "Not sent. Garmin didn't answer."
  createdAt: number;
  updatedAt: number;
  sentAt: number | null;         // Garmin accepted it
  onWatchAt: number | null;      // the watch has it
};

export type FailureKind = 'timeout' | 'offline' | 'refused';

/** Automatic retries: 30 s, 1, 2, 4, 8, 15, 30 min, then it waits for Resend. */
export const BACKOFF_MS = [30_000, 60_000, 120_000, 240_000, 480_000, 900_000, 1_800_000];

export function backoff(attempts: number): number | null {
  return attempts >= 1 && attempts <= BACKOFF_MS.length ? BACKOFF_MS[attempts - 1] : null;
}

export function failureWords(kind: FailureKind, detail?: string): string {
  if (kind === 'timeout') return 'Not sent. Garmin didn’t answer.';
  if (kind === 'offline') return 'Not sent. No connection.';
  return `Not sent. Garmin said no${detail ? `: ${detail}` : '.'}`;
}

/** The confirm: every line becomes an operation, sending now. */
export function confirmOps(drafts: OpDraft[], batch: string, now: number): WatchOp[] {
  return drafts.map((d, i) => ({
    ...d, id: `${batch}:${i}`, batch, state: 'sending', attempts: 0, nextAttemptAt: null, error: null,
    createdAt: now, updatedAt: now, sentAt: null, onWatchAt: null,
  }));
}

/** Ready to try now: newly confirmed, Resend pressed, or a failure whose backoff has passed. */
export function isDue(op: WatchOp, now: number): boolean {
  if (op.state === 'sending') return op.nextAttemptAt == null || op.nextAttemptAt <= now;
  return op.state === 'failed' && op.nextAttemptAt != null && op.nextAttemptAt <= now;
}

export function accepted(op: WatchOp, synced: boolean, now: number): WatchOp {
  return { ...op, state: synced ? 'on_watch' : 'changed_pending_sync', attempts: op.attempts + 1, error: null,
    nextAttemptAt: null, sentAt: now, onWatchAt: synced ? now : null, updatedAt: now };
}

export function failed(op: WatchOp, kind: FailureKind, now: number, detail?: string): WatchOp {
  const attempts = op.attempts + 1;
  const wait = kind === 'refused' ? null : backoff(attempts);
  return { ...op, state: 'failed', attempts, error: failureWords(kind, detail),
    nextAttemptAt: wait == null ? null : now + wait, updatedAt: now };
}

export function onWatch(op: WatchOp, now: number): WatchOp {
  return op.state === 'changed_pending_sync' ? { ...op, state: 'on_watch', onWatchAt: now, updatedAt: now } : op;
}

export function resend(op: WatchOp, now: number): WatchOp {
  return op.state === 'failed' ? { ...op, state: 'sending', nextAttemptAt: null, updatedAt: now } : op;
}

export const isSettled = (op: WatchOp) => op.state === 'on_watch' || op.state === 'changed_pending_sync';

/**
 * What a screen reader says about a confirmed batch where it stands, the same words the
 * screens show: "Sending to Forerunner 965", "Not sent. Garmin didn't answer.", "Changed ·
 * arrives at next sync", "On your watch", or "Not on your watch" for a removal. Null for
 * an empty batch.
 */
export function batchWords(ops: WatchOp[], device: string | null): string | null {
  if (!ops.length) return null;
  if (ops.some((o) => o.state === 'sending')) return `Sending to ${device ?? 'your watch'}`;
  const failure = ops.find((o) => o.state === 'failed');
  if (failure) return failure.error ?? 'Not sent.';
  if (ops.every((o) => o.kind === 'remove')) return 'Not on your watch';
  return ops.some((o) => o.state === 'changed_pending_sync') ? 'Changed · arrives at next sync' : 'On your watch';
}

const hhmm = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** The latest operation for a session, if any. */
export function latestFor(ops: WatchOp[], sessionId: string): WatchOp | null {
  let best: WatchOp | null = null;
  for (const op of ops) if (op.sessionId === sessionId && (!best || op.updatedAt >= best.updatedAt)) best = op;
  return best;
}

/**
 * What a session's watch status reads, given the queue: the latest operation wins
 * over what the server last said. A removal that went through leaves it "Not on
 * your watch"; one that failed is still on the watch, with the failure to say so.
 */
export function watchFor(ops: WatchOp[], sessionId: string, base: Watch | null): Watch | null {
  const op = latestFor(ops, sessionId);
  if (!op) return base;
  if (op.kind === 'remove' && isSettled(op)) return { state: 'not_sent', at: null };
  const at = op.state === 'on_watch' && op.onWatchAt ? hhmm(op.onWatchAt) : op.state === 'failed' ? hhmm(op.updatedAt) : null;
  return { state: op.state, at };
}
