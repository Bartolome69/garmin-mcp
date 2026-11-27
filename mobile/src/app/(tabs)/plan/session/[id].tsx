import { Stack, useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable } from 'react-native';

import { findSession } from '../../../../features/plan/model';
import { usePlan } from '../../../../features/plan/PlanProvider';
import { SessionDetailView } from '../../../../features/plan/SessionDetailView';
import { sendProposal, skipProposal, unwatchProposal } from '../../../../features/watch/proposals';
import { useWatch } from '../../../../features/watch/WatchProvider';
import { useFlags } from '../../../../flags/FlagsProvider';
import { Screen } from '../../../../ui/Screen';
import { Text } from '../../../../ui/Text';

export default function SessionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const plan = usePlan();
  const watch = useWatch();
  const { flags } = useFlags();
  const router = useRouter();
  const focused = useIsFocused();
  const [step, setStep] = useState(0);
  const session = plan.shown ? findSession(plan.shown, id) : null;
  const op = watch.opFor(id);
  const next = (kind: string) => router.push({ pathname: '/plan/next', params: { kind, id } });
  const confirm = (p: Parameters<typeof watch.stage>[0]) => { watch.stage(p); router.push({ pathname: '/watch/confirm', params: { id: p.id } }); };
  // New targets, once sent, are what's on the watch: the steps go back to as planned.
  useEffect(() => { if (op?.batch.startsWith('adjust:')) setStep(0); }, [op?.batch]);

  if (!session) {
    return <Screen testID="session-missing"><Text variant="body" tone="muted">This session isn’t in your plan any more.</Text></Screen>;
  }
  return (
    <>
      {/* Edit opens the builder (milestone 6). No other top-right action. */}
      <Stack.Screen options={{ headerRight: () => (
        <Pressable accessibilityRole="button" onPress={() => next('builder')} hitSlop={12}>
          <Text variant="body">Edit</Text>
        </Pressable>
      ) }} />
      <SessionDetailView
        session={session}
        basis={plan.shown!.basis}
        coach={flags.coach_enabled}
        step={step}
        onStep={setStep}
        device={watch.connection.device}
        error={op?.state === 'failed' ? op.error : null}
        retrying={op?.state === 'failed' && op.nextAttemptAt != null}
        brand={focused}
        actions={{
          send: () => {
            const pace = session.detail?.steps.some((s) => (s.kind === 'repeat' ? s.steps : [s]).some((r) => r.target.kind === 'pace'));
            confirm(sendProposal(session, step, pace ? 'pace /km' : 'as planned'));
          },
          resend: () => watch.ops.filter((o) => o.sessionId === id && o.state === 'failed').forEach((o) => watch.resend(o.id)),
          remove: () => confirm(unwatchProposal([session])),
          trouble: () => next('trouble'),
          move: () => router.push({ pathname: '/plan/move', params: { id } }),
          swap: () => router.push({ pathname: '/plan/move', params: { id, mode: 'swap' } }),
          skip: () => {
            const p = skipProposal(session);
            watch.stage(p);
            router.push({ pathname: '/plan/new-week', params: { id: p.id } });
          },
        }}
      />
    </>
  );
}
