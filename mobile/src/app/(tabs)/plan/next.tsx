import { useLocalSearchParams } from 'expo-router';

import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// Where Plan's actions lead, until the milestones that build them. Nothing is
// written to the watch from here.
const COMING: Record<string, string> = {
  move: 'The watch confirm for this move arrives in milestone 5.',
  send: 'Sending to the watch arrives in milestone 5.',
  adjust: 'Sending the new targets to the watch arrives in milestone 5.',
  life: 'Life happened, the new week and the watch confirm arrive in milestone 5.',
  trim: 'Trimming a week, with the new week and the watch confirm, arrives in milestone 5.',
  swap: 'Swapping a session, with the watch confirm, arrives in milestone 5.',
  skip: 'Skipping a session, with the watch confirm, arrives in milestone 5.',
  builder: 'The workout builder arrives in milestone 6.',
  run: 'Post-run analysis arrives in milestone 7.',
  plus: 'Plus arrives in milestone 10.',
  change: 'Changing the plan arrives with Life happened in milestone 5.',
};

export default function PlanNext() {
  const { kind } = useLocalSearchParams<{ kind?: string }>();
  return (
    <Screen testID="plan-next">
      <Text variant="body" tone="muted">{COMING[kind ?? ''] ?? 'Coming in a later milestone.'}</Text>
    </Screen>
  );
}
