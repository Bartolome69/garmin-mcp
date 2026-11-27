import { useIsFocused, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { useWindowDimensions } from 'react-native';

import { freshness } from '../../../data/freshness';
import { weekIndexOf } from '../../../features/plan/model';
import { proposalWords } from '../../../features/plan/moves';
import { usePlan } from '../../../features/plan/PlanProvider';
import type { PlanTab } from '../../../features/plan/PlanTabs';
import { PlanView } from '../../../features/plan/PlanView';
import { TodayEmpty } from '../../../features/today/TodayView';
import { useFlags } from '../../../flags/FlagsProvider';

function useNow(everyMs = 60000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), everyMs); return () => clearInterval(t); }, [everyMs]);
  return now;
}

export default function PlanScreen() {
  const plan = usePlan();
  const { flags } = useFlags();
  const router = useRouter();
  // A screen behind an open sheet loses its brand fill (build prompt, section 2).
  const focused = useIsFocused();
  const { width } = useWindowDimensions();
  const fresh = freshness(plan.fetchedAt, useNow());
  const [tab, setTab] = useState<PlanTab>('Week');
  const [index, setIndex] = useState<number | null>(null);
  const vm = plan.shown;

  if (!vm) return <TodayEmpty error={plan.error} onRetry={plan.refresh} />;
  const shownIndex = index ?? weekIndexOf(vm, vm.currentWeek);
  const next = (kind: string, extra: Record<string, string> = {}) => router.push({ pathname: '/plan/next', params: { kind, ...extra } });
  return (
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
        open: (s) => (s.done ? next('run', { id: s.id }) : router.push({ pathname: '/plan/session/[id]', params: { id: s.id } })),
        move: (id, to) => { plan.move(id, to); },
        moveAnyway: plan.confirmPending,
        keep: plan.clearPending,
        undo: plan.clearProposal,
        review: () => next('move'),
        life: () => next('life'),
        newWorkout: () => next('builder'),
        refresh: plan.refresh,
        trim: () => next('trim'),
        plus: () => next('plus'),
        change: () => next('change'),
      }}
    />
  );
}
