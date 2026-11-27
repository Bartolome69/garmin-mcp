// The Move sheet: the days of the session's week. Choosing one runs the same check
// as dragging (the back-to-back warning, if any, shows on the week), then returns
// to the week, where the move waits for the confirm. With mode=swap, the session
// trades days with the one on the day chosen, through the new week and the confirm.
import { useLocalSearchParams, useRouter } from 'expo-router';
import { View } from 'react-native';

import { findSession } from '../../../features/plan/model';
import { canMove, moveTargets } from '../../../features/plan/moves';
import { usePlan } from '../../../features/plan/PlanProvider';
import { swapProposal } from '../../../features/watch/proposals';
import { useWatch } from '../../../features/watch/WatchProvider';
import { Button } from '../../../ui/Button';
import { Text } from '../../../ui/Text';

export default function MoveSheet() {
  const { id, mode } = useLocalSearchParams<{ id: string; mode?: string }>();
  const plan = usePlan();
  const watch = useWatch();
  const router = useRouter();
  const swap = mode === 'swap';
  const vm = plan.shown;
  const s = vm ? findSession(vm, id) : null;
  if (!vm || !s) return null;
  return (
    <View testID="move-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">{`${swap ? 'Swap' : 'Move'} ${s.title}`}</Text>
      <Text variant="footnote" tone="muted">Your call. Any change to the watch is confirmed first.</Text>
      <View style={{ gap: 8 }}>
        {moveTargets(vm, id).map((t) => {
          const there = vm.weeks.flatMap((w) => w.days).find((d) => d.date === t.date)?.sessions.find((x) => canMove(x, vm.today));
          const label = swap && there ? `${t.label} · ${there.title}` : t.label;
          return (
            <Button key={t.date} label={label} disabled={t.disabled || (swap && !there)}
              onPress={() => {
                if (swap && there) {
                  const p = swapProposal(vm, s, there);
                  watch.stage(p);
                  router.back();
                  router.push({ pathname: '/plan/new-week', params: { id: p.id } });
                } else {
                  plan.move(id, t.date);
                  router.dismissTo('/plan');
                }
              }} />
          );
        })}
      </View>
      <Button label="Cancel" onPress={() => router.back()} />
    </View>
  );
}
