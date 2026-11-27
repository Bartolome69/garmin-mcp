/**
 * @jest-environment node
 */
// The watch queue (milestone 5): the state machine, backoff, the store on real
// SQLite, and the Garmin send contract with mocked success, timeout and failure.
import { describe, expect, it } from '@jest/globals';

import { migratedNodeDb } from '../../db/testing/nodeDb';
import { PERSONA_PLAN } from '../plan/fixture';
import { findSession } from '../plan/model';
import { opWords, type OpDraft } from './model';
import { fixtureProposal, moveProposal, sendProposal, skipProposal, unwatchProposal } from './proposals';
import { accepted, backoff, BACKOFF_MS, confirmOps, failed, failureWords, isDue, onWatch, resend, watchFor, type WatchOp } from './queue';
import { sqlStore } from './store';
import { FakeGarmin, SendFailure, withTimeout } from './transport';

const TEMPO = findSession(PERSONA_PLAN, '2026-11-27-tempo')!;
const draft: OpDraft = sendProposal(TEMPO).ops[0];

describe('the state machine', () => {
  it('a confirmed line is sending at once, and due', () => {
    const [op] = confirmOps([draft], 'b1', 1000);
    expect(op).toMatchObject({ id: 'b1:0', batch: 'b1', state: 'sending', attempts: 0, nextAttemptAt: null, kind: 'add' });
    expect(isDue(op, 1000)).toBe(true);
  });

  it('accepted but not yet synced reads "Changed · arrives at next sync", then "On your watch" once it syncs', () => {
    const [op] = confirmOps([draft], 'b1', 1000);
    const sent = accepted(op, false, 2000);
    expect(sent).toMatchObject({ state: 'changed_pending_sync', sentAt: 2000, onWatchAt: null, attempts: 1 });
    expect(onWatch(sent, 9000)).toMatchObject({ state: 'on_watch', onWatchAt: 9000 });
    expect(accepted(op, true, 2000)).toMatchObject({ state: 'on_watch', onWatchAt: 2000 });
  });

  it('a timeout fails in words and retries on its own, backing off', () => {
    let [op] = confirmOps([draft], 'b1', 0);
    const waits: number[] = [];
    for (let i = 0; i < BACKOFF_MS.length; i++) {
      op = failed(op, 'timeout', 0);
      waits.push(op.nextAttemptAt!);
      expect(isDue(op, op.nextAttemptAt! - 1)).toBe(false);
      expect(isDue(op, op.nextAttemptAt!)).toBe(true);
    }
    expect(op.error).toBe('Not sent. Garmin didn’t answer.');
    expect(waits).toEqual(BACKOFF_MS);
    op = failed(op, 'timeout', 0);
    expect(op.nextAttemptAt).toBeNull(); // then it waits for Resend
    expect(isDue(resend(op, 5), 5)).toBe(true);
  });

  it('a refusal waits for Resend; no connection retries', () => {
    const [op] = confirmOps([draft], 'b1', 0);
    expect(failed(op, 'refused', 0, 'that workout is gone')).toMatchObject({ state: 'failed', nextAttemptAt: null,
      error: 'Not sent. Garmin said no: that workout is gone' });
    expect(failed(op, 'offline', 0)).toMatchObject({ nextAttemptAt: 30000, error: 'Not sent. No connection.' });
    expect(failureWords('refused')).toBe('Not sent. Garmin said no.');
    expect(backoff(0)).toBeNull();
  });

  it("a session's status is its latest operation; a removal that went through reads Not on your watch", () => {
    const base = { state: 'on_watch' as const, at: '20:14' };
    expect(watchFor([], TEMPO.id, base)).toBe(base);
    const [rm] = confirmOps(unwatchProposal([TEMPO]).ops, 'b2', 1000);
    expect(watchFor([rm], TEMPO.id, base)).toEqual({ state: 'sending', at: null });
    expect(watchFor([accepted(rm, false, 2000)], TEMPO.id, base)).toEqual({ state: 'not_sent', at: null });
    const failedRm = failed(rm, 'timeout', new Date(2026, 10, 27, 20, 30).getTime());
    expect(watchFor([failedRm], TEMPO.id, base)).toEqual({ state: 'failed', at: '20:30' });
  });
});

describe('proposals built on the device', () => {
  it('a move is a removal from the old day and an addition on the new one', () => {
    const p = moveProposal(PERSONA_PLAN, TEMPO, '2026-11-28');
    expect(p.ops.map(opWords)).toEqual(['Remove · Fri 27 · 3 × 2 km at threshold', 'Add · Sat 28 · 3 × 2 km at threshold']);
    expect(p.subtitle).toBe('Tempo moves from Fri to Sat. This week’s kilometres stay the same.');
    expect(p.rule).toBe('Checked: no two hard days in a row.');
    expect(moveProposal(PERSONA_PLAN, TEMPO, '2026-12-03', true).rule).toBe('Two hard days in a row: you chose to move it anyway.');
  });

  it('a skip takes it off and says the session’s kilometres', () => {
    const p = skipProposal(TEMPO);
    expect(p.ops.map(opWords)).toEqual(['Remove · Fri 27 · 3 × 2 km at threshold']);
    expect(p.subtitle).toBe('10 km less this week.');
  });

  it('new targets replace the session on the watch, and say how', () => {
    expect(opWords(sendProposal(TEMPO, -1).ops[0])).toBe('Replace · Fri 27 · 3 × 2 km at threshold with 3 × 2 km at threshold, 2% easier');
    expect(sendProposal(TEMPO).changesPlan).toBe(false);
  });

  it('the server’s proposals (the persona fixture) carry the engine’s words', () => {
    const p = fixtureProposal({ kind: 'life', reason: 'unwell', since: '2026-11-27' }, false)!;
    expect(p.kicker).toBe('Not feeling 100% · since today');
    expect(p.subtitle).toBe('54 → 38 km this week. Next week unchanged.');
    expect(p.ops.map(opWords)).toEqual([
      'Replace · Fri 27 · 3 × 2 km at threshold with Easy 3 km',
      'Replace · Sun 29 · Long 18 km easy with Long 9 km easy',
    ]);
    expect(p.coachNote).toBeUndefined();
    expect(fixtureProposal({ kind: 'easier' }, true)!.coachNote).toBeTruthy();
  });
});

describe('the store, on SQLite', () => {
  it('keeps operations through a restart, every field', async () => {
    const db = await migratedNodeDb();
    const store = sqlStore(db, () => 5000);
    const ops = confirmOps(moveProposal(PERSONA_PLAN, TEMPO, '2026-11-28').ops, 'b1', 1000);
    await store.save(ops);
    const later = failed(ops[1], 'timeout', 2000);
    await store.save([accepted(ops[0], false, 1500), later]);
    const loaded = await sqlStore(db, () => 6000).load();
    expect(loaded).toHaveLength(2);
    expect(loaded[0]).toEqual(accepted(ops[0], false, 1500));
    expect(loaded[1]).toEqual(later);
  });

  it('keeps settled operations a day, then lets them go', async () => {
    const db = await migratedNodeDb();
    const [op] = confirmOps([draft], 'b1', 0);
    await sqlStore(db).save([accepted(op, true, 1000)]);
    expect(await sqlStore(db, () => 1000 + 3600_000).load()).toHaveLength(1);
    expect(await sqlStore(db, () => 1000 + 25 * 3600_000).load()).toHaveLength(0);
  });

  it('the table refuses a state outside the six', async () => {
    const db = await migratedNodeDb();
    const [op] = confirmOps([draft], 'b1', 0);
    await expect(sqlStore(db).save([{ ...op, state: 'done' as WatchOp['state'] }])).rejects.toThrow();
  });
});

describe('the Garmin send contract (mocked)', () => {
  const op = confirmOps([draft], 'b1', 0)[0];

  it('success: accepted, synced or not', async () => {
    await expect(new FakeGarmin(['ok']).send(op)).resolves.toEqual({ synced: false });
    await expect(new FakeGarmin(['synced']).send(op)).resolves.toEqual({ synced: true });
  });

  it('timeout: a send that never answers gives up as a timeout', async () => {
    const never = new Promise<{ synced: boolean }>(() => {});
    await expect(withTimeout(never, 20)).rejects.toMatchObject({ kind: 'timeout' });
  });

  it('failure: no connection, and a refusal with Garmin’s reason', async () => {
    await expect(new FakeGarmin(['offline']).send(op)).rejects.toBeInstanceOf(SendFailure);
    await expect(new FakeGarmin(['refused']).send(op)).rejects.toMatchObject({ kind: 'refused', detail: 'that workout is gone from Garmin Connect' });
  });

  it('the watch syncs what Garmin accepted after a poll or two', async () => {
    const g = new FakeGarmin([], 'ok', 2);
    const sent = accepted(op, false, 1);
    expect(await g.synced([sent])).toEqual([]);
    expect(await g.synced([sent])).toEqual([sent.id]);
  });
});
