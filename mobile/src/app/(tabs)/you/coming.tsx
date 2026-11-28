import { COMING } from '../../../features/plus/coming';
import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// Advanced merge rules for a Plus runner, in You's own stack (Source priority's
// row), until the engine exists. Nothing is written from here.
export default function YouComing() {
  return (
    <Screen testID="you-coming">
      <Text variant="body" tone="muted">{COMING.merge_rules}</Text>
    </Screen>
  );
}
