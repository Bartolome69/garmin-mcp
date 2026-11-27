import { Pressable, View } from 'react-native';

import { MIN_TOUCH } from '../../ui/Button';

import { Card } from '../../ui/Card';
import { StructureBar } from '../../ui/session/StructureBar';
import { TypeChip } from '../../ui/session/TypeChip';
import { WatchStatus } from '../../ui/session/WatchStatus';
import { Text } from '../../ui/Text';
import type { TodaySession } from './model';

/** Today's session: chip, title (opens the session, no chevron), numbers, structure, watch status. */
export function SessionCard({ session, onOpen }: { session: TodaySession; onOpen: () => void }) {
  return (
    <Card testID="session-card">
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <TypeChip type={session.type} label={session.typeLabel} />
        <WatchStatus state={session.watch.state} at={session.watch.at} />
      </View>
      <Pressable accessibilityRole="link" accessibilityHint="Opens the session" onPress={onOpen}
        hitSlop={{ top: (MIN_TOUCH - 28) / 2, bottom: (MIN_TOUCH - 28) / 2 }}>
        <Text variant="title" numeric>{session.title}</Text>
      </Pressable>
      <Text variant="footnote" tone="muted" numeric style={{ marginTop: -4 }}>{session.meta}</Text>
      <StructureBar parts={session.structure} type={session.type} legend={session.legend} />
    </Card>
  );
}
