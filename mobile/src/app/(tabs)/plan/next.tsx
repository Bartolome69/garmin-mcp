import { useLocalSearchParams } from 'expo-router';

import { COMING, COMING_NOTE } from '../../../features/plus/coming';
import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// SKETCH: Block-ios (plan styles) — advanced plan styles have no engine; building one is the
// owner's decision (DECISIONS, milestone 10). A Plus runner who opens a locked style lands
// here and is told it comes in an update. Nothing is written to the watch from here.
export default function PlanNext() {
  const { kind } = useLocalSearchParams<{ kind?: string }>();
  return (
    <Screen testID="plan-next">
      <Text variant="body" tone="muted">{COMING[kind ?? ''] ?? `${COMING_NOTE}.`}</Text>
    </Screen>
  );
}
