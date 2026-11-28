// Keeps the widgets' snapshot in step with what Today draws: whenever Today, the
// watch queue or Plus changes, the snapshot is rebuilt and, if it draws anything
// different, written and the widgets redrawn. Draws nothing itself.
import { useEffect, useMemo, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { useDb } from '../../db/DbContext';
import { kv } from '../../db/kv';
import type { Cached } from '../../data/resource';
import { useFlags } from '../../flags/FlagsProvider';
import type { PlanVM } from '../plan/model';
import type { TodayVM } from '../today/model';
import { useEntitlement, usePlus } from '../plus/EntitlementsProvider';
import { useToday } from '../today/TodayProvider';
import { applyToToday } from '../watch/apply';
import { useWatch } from '../watch/WatchProvider';
import { defaultWidgetBridge, type WidgetBridge } from './bridge';
import { buildSnapshot, sameDrawing, type WidgetSnapshot } from './snapshot';

const REBUILD_EVERY_MS = 15 * 60 * 1000;

export function WidgetSync({ bridge: given, clock = Date.now }: { bridge?: WidgetBridge; clock?: () => number }) {
  const bridge = useMemo(() => given ?? defaultWidgetBridge(), [given]);
  const today = useToday();
  const watch = useWatch();
  const { plus, ready, entitlement } = useEntitlement();
  const trialUsed = !plus && entitlement.lapsed != null;
  const db = useDb();
  const { flags } = useFlags();
  const [block, setBlock] = useState<PlanVM['block']>(null);
  const [tick, setTick] = useState(0);
  const last = useRef<WidgetSnapshot | null>(null);

  // The plan's block for the phase name, from the phone's copy (the Plan tab may not have opened yet).
  useEffect(() => {
    if (!db) return;
    const key = `plan:${flags.coach_enabled ? 'coach' : 'plain'}:${plus ? 'plus' : 'free'}`;
    kv(db).get<Cached<PlanVM>>(key).then((c) => setBlock(c?.data.block ?? null)).catch(() => {});
  }, [db, flags.coach_enabled, plus, today.fetchedAt]);

  // Rebuilt now and then, and on coming back, so "Updated" appears once the data is old.
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), REBUILD_EVERY_MS);
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active' || s === 'background') setTick((n) => n + 1); });
    return () => { clearInterval(t); sub.remove(); };
  }, []);

  useEffect(() => {
    // Plus not known yet (the cached answer still being read): a Plus runner's widgets keep what they have.
    if (!ready) return;
    const data = today.data ? applyToToday(today.data, watch.applied, watch.ops) : null;
    // Still loading: keep what the widgets have rather than flash "Open Daash".
    if (plus && !data && !today.error) return;
    const next = buildSnapshot({ today: data && today.fetchedAt != null ? { data, fetchedAt: today.fetchedAt } : null, block, plus, trialUsed, now: clock() });
    if (sameDrawing(last.current, next)) return;
    last.current = next;
    bridge.write(next).then(() => bridge.reload()).catch(() => { last.current = null; });
  }, [today.data, today.fetchedAt, today.error, watch.applied, watch.ops, block, plus, trialUsed, ready, tick, bridge, clock]);

  return null;
}

/**
 * The widgets as they'd draw now, for the setup screen: from the phone's cached
 * Today and plan (the You tab has no Today of its own), always as for Plus, so a
 * Free runner sees the preview behind the lock.
 */
export function usePreviewSnapshot(clock: () => number = Date.now): WidgetSnapshot {
  const db = useDb();
  const watch = useWatch();
  const plus = usePlus();
  const { flags } = useFlags();
  const [cached, setCached] = useState<{ today: Cached<TodayVM> | null; block: PlanVM['block'] }>({ today: null, block: null });
  useEffect(() => {
    if (!db) return;
    const coach = flags.coach_enabled ? 'coach' : 'plain';
    Promise.all([
      kv(db).get<Cached<TodayVM>>(`today:${coach}`),
      kv(db).get<Cached<PlanVM>>(`plan:${coach}:${plus ? 'plus' : 'free'}`),
    ]).then(([today, plan]) => setCached({ today, block: plan?.data.block ?? null })).catch(() => {});
  }, [db, flags.coach_enabled, plus]);
  const data = cached.today ? applyToToday(cached.today.data, watch.applied, watch.ops) : null;
  return buildSnapshot({ today: data && cached.today ? { data, fetchedAt: cached.today.fetchedAt } : null, block: cached.block, plus: true, now: clock() });
}
