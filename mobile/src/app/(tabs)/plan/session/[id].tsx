import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { Pressable } from 'react-native';

import { findSession } from '../../../../features/plan/model';
import { usePlan } from '../../../../features/plan/PlanProvider';
import { SessionDetailView } from '../../../../features/plan/SessionDetailView';
import { useFlags } from '../../../../flags/FlagsProvider';
import { Screen } from '../../../../ui/Screen';
import { Text } from '../../../../ui/Text';

export default function SessionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const plan = usePlan();
  const { flags } = useFlags();
  const router = useRouter();
  const [step, setStep] = useState(0);
  const session = plan.shown ? findSession(plan.shown, id) : null;
  const next = (kind: string) => router.push({ pathname: '/plan/next', params: { kind, id } });

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
        actions={{
          send: () => next(step ? 'adjust' : 'send'),
          move: () => router.push({ pathname: '/plan/move', params: { id } }),
          swap: () => next('swap'),
          skip: () => next('skip'),
        }}
      />
    </>
  );
}
