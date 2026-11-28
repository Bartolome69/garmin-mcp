import { useLocalSearchParams } from 'expo-router';

import { COMING } from '../../../features/plus/coming';
import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// Where Plan's actions lead, until the milestones that build them. Nothing is
// written to the watch from here.
export default function PlanNext() {
  const { kind } = useLocalSearchParams<{ kind?: string }>();
  return (
    <Screen testID="plan-next">
      <Text variant="body" tone="muted">{COMING[kind ?? ''] ?? 'Coming in a later milestone.'}</Text>
    </Screen>
  );
}
