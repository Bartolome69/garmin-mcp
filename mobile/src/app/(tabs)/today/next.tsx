import { useLocalSearchParams } from 'expo-router';

import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// Where Today's actions lead: the session detail (milestone 4) and the shared
// diff and watch confirm (milestone 5). Nothing is written to the watch from here.
const COMING: Record<string, string> = {
  easier: 'The new week and the watch confirm arrive in milestone 5.',
  swap: 'The new week and the watch confirm arrive in milestone 5.',
  rest: 'The new week and the watch confirm arrive in milestone 5.',
};

export default function TodayNext() {
  const { kind } = useLocalSearchParams<{ kind?: string }>();
  return (
    <Screen testID="today-next">
      <Text variant="body" tone="muted">{COMING[kind ?? ''] ?? 'Coming in a later milestone.'}</Text>
    </Screen>
  );
}
