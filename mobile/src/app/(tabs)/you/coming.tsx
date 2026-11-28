import { COMING } from '../../../features/plus/coming';
import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

// SKETCH: P1-Priority-ios (advanced merge rules) — no merge-rule engine yet; building one is
// the owner's decision (DECISIONS, milestone 10). A Plus runner opening Source priority's
// row lands here, in You's own stack. Nothing is written from here.
export default function YouComing() {
  return (
    <Screen testID="you-coming">
      <Text variant="body" tone="muted">{COMING.merge_rules}</Text>
    </Screen>
  );
}
