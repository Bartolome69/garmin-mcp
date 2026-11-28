// The plan reveal, then "Send week 1 to my watch": through the watch confirm like
// every other write, and on to the first workout landing only once it's confirmed.
import { Redirect, useIsFocused, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';

import { failureKind, type FailureKind } from '../../data/resource';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { makePlan } from '../../features/onboarding/source';
import { RevealView, RevealWait } from '../../features/onboarding/views';
import { weekOneProposal } from '../../features/onboarding/week1';
import { useWatch } from '../../features/watch/WatchProvider';
import { useFlags } from '../../flags/FlagsProvider';
import { openGarminSignIn } from '../../ui/StateViews';

export default function RevealScreen() {
  const ob = useOnboarding();
  const watch = useWatch();
  const { flags } = useFlags();
  const router = useRouter();
  const focused = useIsFocused();
  const [failure, setFailure] = useState<FailureKind | null>(null);
  // The batches on the queue when week 1 was staged: a new one is this confirm's.
  const staged = useRef<{ id: string; before: Set<string> } | null>(null);
  const moved = useRef(false);
  const { result, goal, reveal, setReveal } = ob;

  const load = useCallback(() => {
    if (!result || !goal) return;
    setFailure(null);
    makePlan(result, goal, flags.coach_enabled).then(setReveal).catch((err) => setFailure(failureKind(err)));
  }, [result, goal, flags.coach_enabled, setReveal]);
  // A plan already made from these answers is kept; changed answers drop it (OnboardingProvider).
  useEffect(() => { if (!reveal) load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Confirmed on the sheet: the queue has a new batch for week 1. Only then on to the landing.
  useEffect(() => {
    const s = staged.current;
    if (!s || moved.current) return;
    if (watch.ops.some((o) => o.batch.startsWith(`${s.id}@`) && !s.before.has(o.batch))) {
      moved.current = true;
      router.replace('/onboarding/landed');
    }
  }, [watch.ops, router]);

  if (!result) return <Redirect href="/onboarding/paces" />;
  if (!goal) return <Redirect href="/onboarding/goal" />;
  if (!reveal) {
    return <RevealWait failure={failure ?? 'loading'} brand={focused} onRetry={load} onConnect={openGarminSignIn} />;
  }
  return (
    <RevealView r={reveal} coach={flags.coach_enabled} brand={focused} onSend={() => {
      const p = weekOneProposal(reveal);
      staged.current = { id: p.id, before: new Set(watch.ops.map((o) => o.batch)) };
      watch.stage(p);
      router.push({ pathname: '/watch/confirm', params: { id: p.id } });
    }} />
  );
}
