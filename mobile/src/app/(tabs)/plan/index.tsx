import { useIsFocused, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, useWindowDimensions } from 'react-native';

import { freshness } from '../../../data/freshness';
import { EmptyPlan } from '../../../features/plan/EmptyPlan';
import { findSession, weekIndexOf } from '../../../features/plan/model';
import { proposalWords } from '../../../features/plan/moves';
import { usePlan } from '../../../features/plan/PlanProvider';
import type { PlanTab } from '../../../features/plan/PlanTabs';
import { PlanView } from '../../../features/plan/PlanView';
import { TodayEmpty } from '../../../features/today/TodayView';
import { moveProposal } from '../../../features/watch/proposals';
import { useWatch } from '../../../features/watch/WatchProvider';
import { useFlags } from '../../../flags/FlagsProvider';
import { FailureBanner } from '../../../ui/StateViews';

function useNow(everyMs = 60000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), everyMs); return () => clearInterval(t); }, [everyMs]);
  return now;
}

export default function PlanScreen() {
  const plan = usePlan();
  const watch = useWatch();
  const { flags } = useFlags();
  const router = useRouter();
  // A screen behind an open sheet loses its brand fill (build prompt, section 2).
  const focused = useIsFocused();
  const { width } = useWindowDimensions();
  const fresh = freshness(plan.fetchedAt, useNow());
  const [tab, setTab] = useState<PlanTab>('Week');
  const [index, setIndex] = useState<number | null>(null);
  const vm = plan.shown;

  if (!vm) return <TodayEmpty error={plan.error} failure={plan.failure} what="your plan" testID="plan" onRetry={plan.refresh} />;
  // A new runner with no plan: the empty state, not a blank week.
  if (!vm.weeks.length) {
    return <EmptyPlan brand={focused} onCreate={() => router.push('/onboarding/goal?from=plan')} onWeekByWeek={() => router.push('/plan/builder')} />;
  }
  const shownIndex = index ?? weekIndexOf(vm, vm.currentWeek);
  const next = (kind: string, extra: Record<string, string> = {}) => router.push({ pathname: '/plan/next', params: { kind, ...extra } });
  return (
    <>
    <FailureBanner failure={plan.failure} />
    <PlanView
      vm={vm}
      tab={tab}
      onTab={setTab}
      index={shownIndex}
      onIndex={setIndex}
      pending={plan.pending}
      proposalText={plan.proposal ? proposalWords(vm, plan.proposal) : null}
      plus={flags.plus}
      brand={focused}
      fresh={fresh}
      refreshing={plan.refreshing}
      width={width}
      actions={{
        open: (s) => router.push(s.done ? { pathname: '/plan/run/[id]', params: { id: s.id } } : { pathname: '/plan/session/[id]', params: { id: s.id } }),
        move: (id, to) => { plan.move(id, to); },
        moveAnyway: plan.confirmPending,
        keep: plan.clearPending,
        undo: plan.clearProposal,
        review: () => {
          const p = plan.proposal;
          const s = p ? findSession(vm, p.id) : null;
          if (!p || !s) return;
          const staged = moveProposal(vm, { ...s, date: p.from }, p.to, plan.warned);
          watch.stage(staged);
          router.push({ pathname: '/plan/new-week', params: { id: staged.id } });
        },
        life: () => router.push('/plan/life'),
        newWorkout: () => router.push('/plan/builder'),
        refresh: plan.refresh,
        trim: async () => {
          const week = vm.trends.load.weeks.find((w) => w.trim);
          if (!week) return;
          try {
            const p = await watch.propose({ kind: 'trim', week: week.starts });
            router.push({ pathname: '/plan/new-week', params: { id: p.id } });
          } catch (err) {
            Alert.alert('Couldn’t work out the trim', err instanceof Error ? err.message : 'Try again in a moment.');
          }
        },
        plus: () => next('plus'),
        change: () => router.push('/plan/life'),
      }}
    />
    </>
  );
}
