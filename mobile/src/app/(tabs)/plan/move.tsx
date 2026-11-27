// The Move sheet: the days of the session's week. Choosing one runs the same check
// as dragging (the back-to-back warning, if any, shows on the week), then returns
// to the week, where the move waits for the confirm.
import { useLocalSearchParams, useRouter } from 'expo-router';
import { View } from 'react-native';

import { findSession } from '../../../features/plan/model';
import { moveTargets } from '../../../features/plan/moves';
import { usePlan } from '../../../features/plan/PlanProvider';
import { Button } from '../../../ui/Button';
import { Text } from '../../../ui/Text';

export default function MoveSheet() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const plan = usePlan();
  const router = useRouter();
  const vm = plan.shown;
  const s = vm ? findSession(vm, id) : null;
  if (!vm || !s) return null;
  return (
    <View testID="move-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">{`Move ${s.title}`}</Text>
      <Text variant="footnote" tone="muted">Your call. Any change to the watch is confirmed first.</Text>
      <View style={{ gap: 8 }}>
        {moveTargets(vm, id).map((t) => (
          <Button key={t.date} label={t.label} disabled={t.disabled}
            onPress={() => { plan.move(id, t.date); router.dismissTo('/plan'); }} />
        ))}
      </View>
      <Button label="Cancel" onPress={() => router.back()} />
    </View>
  );
}
