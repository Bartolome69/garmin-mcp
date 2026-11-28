// The Coach's sheets (boards P3-Coach-menu, CoachLimit): the ⋯ menu, filter by run
// or session, the confirm before clearing the conversation, and today's limit. No
// chevrons: none of these rows opens a screen of its own.
import { Pressable, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Icon, type IconName } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import type { ContextTag } from './model';

function Row({ icon, title, sub, onPress, danger = false, testID, first }: {
  icon: IconName; title: string; sub: string; onPress: () => void; danger?: boolean; testID: string; first: boolean;
}) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={`${title}. ${sub}`} onPress={onPress}
      style={{ minHeight: Math.max(MIN_TOUCH, 56), flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 8,
        borderTopWidth: first ? 0 : 1, borderTopColor: colors.hairline }}>
      <Icon name={icon} tone={danger ? 'brandText' : 'text'} />
      <View style={{ flex: 1 }}>
        <Text variant="body" tone={danger ? 'brandText' : 'text'}>{title}</Text>
        <Text variant="footnote" tone="muted">{sub}</Text>
      </View>
    </Pressable>
  );
}

function List({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  return <View style={{ borderRadius: 12, backgroundColor: colors.sunken, overflow: 'hidden' }}>{children}</View>;
}

export function MenuSheet({ memoryCount, actions }: {
  memoryCount: number;
  actions: { search: () => void; filter: () => void; memory: () => void; clear: () => void; close: () => void };
}) {
  return (
    <View testID="coach-menu" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">One conversation</Text>
      <Text variant="footnote" tone="muted">The Coach keeps one running conversation with you. Questions from a run or a session land here too, tagged so you can find them.</Text>
      <List>
        <Row first testID="menu-search" icon="search" title="Search the conversation" sub="Find anything you asked" onPress={actions.search} />
        <Row first={false} testID="menu-filter" icon="link" title="Filter by run or session" sub="Questions tagged to a run or session" onPress={actions.filter} />
        <Row first={false} testID="menu-memory" icon="info" title="Memory" sub={`${memoryCount} thing${memoryCount === 1 ? '' : 's'} the Coach keeps in mind`} onPress={actions.memory} />
        <Row first={false} testID="menu-clear" icon="close" title="Clear conversation" sub="Memory and your plan stay" onPress={actions.clear} danger />
      </List>
      <Button label="Close" onPress={actions.close} />
    </View>
  );
}

export function FilterSheet({ tags, onPick, onClose }: { tags: ContextTag[]; onPick: (t: ContextTag) => void; onClose: () => void }) {
  return (
    <View testID="coach-filter-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">Filter by run or session</Text>
      {tags.length ? (
        <List>
          {tags.map((t, i) => <Row key={`${t.kind}:${t.id}`} first={i === 0} testID={`filter-${t.id ?? i}`} icon="link" title={t.label.replace(/^About · /, '')}
            sub={t.kind === 'run' ? 'Run' : 'Session'} onPress={() => onPick(t)} />)}
        </List>
      ) : <Text variant="body" tone="muted">No questions about a run or a session yet. Ask from post-run or a session to tag one.</Text>}
      <Button label="Close" onPress={onClose} />
    </View>
  );
}

export function ClearSheet({ onClear, onKeep, error = null, busy = false, offline = false }: {
  onClear: () => void; onKeep: () => void;
  /** The server couldn't clear it: the thread is kept, and this says so. */
  error?: string | null;
  busy?: boolean;
  /** Clearing needs the server (else the thread comes back on the next load). */
  offline?: boolean;
}) {
  return (
    <View testID="coach-clear-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">Clear the conversation?</Text>
      <Text variant="body" tone="muted">Every message in the thread goes. Memory and your plan stay, and nothing changes on your watch.</Text>
      {error || offline ? (
        <View testID="coach-clear-error" accessible accessibilityLiveRegion="polite" style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
          <Icon name="error" tone="brandText" />
          <Text variant="footnote" tone="brandText" style={{ flex: 1 }}>{error ?? 'Clearing needs a connection. The conversation is still here.'}</Text>
        </View>
      ) : null}
      <Button label={busy ? 'Clearing…' : 'Clear conversation'} kind="primary" onPress={onClear} disabled={busy || offline} />
      <Button label="Keep it" onPress={onKeep} />
    </View>
  );
}

export function LimitSheet({ limit, onPlus, onLater }: { limit: number; onPlus: () => void; onLater: () => void }) {
  return (
    <View testID="coach-limit-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">{`That’s today’s ${limit}`}</Text>
      <Text variant="body" tone="muted">{`Free Daash has ${limit} Coach messages a day. Your plan, runs and crew keep working, and a change the Coach already proposed can still be reviewed and sent. Plus removes the limit.`}</Text>
      <Button label="See Plus" kind="primary" onPress={onPlus} />
      <Button label="Tomorrow is fine" onPress={onLater} />
    </View>
  );
}
