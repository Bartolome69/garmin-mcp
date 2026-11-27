import { useLocalSearchParams } from 'expo-router';

import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// Where Plan's actions lead, until the milestones that build them. Nothing is
// written to the watch from here.
const COMING: Record<string, string> = {
  trouble: 'Connection help, and what Daash can and can’t see, arrive with Connections in milestone 8.',
  builder: 'The workout builder arrives in milestone 6.',
  run: 'Post-run analysis arrives in milestone 7.',
  plus: 'Plus arrives in milestone 10.',
};

export default function PlanNext() {
  const { kind } = useLocalSearchParams<{ kind?: string }>();
  return (
    <Screen testID="plan-next">
      <Text variant="body" tone="muted">{COMING[kind ?? ''] ?? 'Coming in a later milestone.'}</Text>
    </Screen>
  );
}
