// Coach memory (Coach on) or Training preferences (Coach off) in You (build prompt,
// section 5.11; boards P1-You-memory-on-ios, P1-You-prefs-off-ios): the same items,
// viewed, edited and deleted either way. On adds Tone, items saved from the chat and
// "Clear memory". Also You's Coach section: the Coach's own switch (where "AI" may be
// said, as its settings) and the row into these items.
import { useState } from 'react';
import { Pressable, ScrollView, Switch, TextInput, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import { MEDICAL } from '../watch/proposals';
import { itemWords, kindLabel, kindsFor, visibleItems, type MemoryItem, type MemoryKind } from './memory';
import { READABLE } from '../../ui/Screen';

function List({ children, testID }: { children: React.ReactNode; testID?: string }) {
  const { colors } = useTheme();
  return <View testID={testID} style={{ borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, overflow: 'hidden' }}>{children}</View>;
}

export function MemoryView({ items, coach, actions }: {
  items: MemoryItem[];
  coach: boolean;
  actions: { edit: (id: string) => void; add: () => void; clear: () => void };
}) {
  const { colors } = useTheme();
  const shown = visibleItems(items, coach);
  return (
    <ScrollView testID="memory" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, padding: 16, gap: 12 }}>
      <Text variant="subhead" tone="muted">
        {coach ? 'Used only when you talk to the Coach. Edit or delete anything; nothing here changes your numbers.' : 'Kept on this phone. Edit or delete anything.'}
      </Text>
      {shown.length ? (
        <List testID="memory-list">
          {shown.map((i, n) => {
            const label = kindLabel(i.kind, coach)!;
            return (
              <View key={i.id} testID={`memory-${i.id}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 14, paddingVertical: 8,
                minHeight: 60, borderTopWidth: n ? 1 : 0, borderTopColor: colors.hairline }}>
                <View style={{ flex: 1 }} accessible accessibilityLabel={`${label}: ${itemWords(i, coach)}`}>
                  <Text variant="footnote" tone="muted">{label}</Text>
                  <Text variant="body" numeric>{itemWords(i, coach)}</Text>
                </View>
                <Pressable testID={`memory-edit-${i.id}`} accessibilityRole="button" accessibilityLabel={`Edit ${label}`} onPress={() => actions.edit(i.id)}
                  style={{ width: MIN_TOUCH, height: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
                  <Icon name="edit" tone="muted" />
                </Pressable>
              </View>
            );
          })}
        </List>
      ) : <Text variant="body" tone="muted" testID="memory-empty">{coach ? 'Nothing kept in mind yet.' : 'No preferences yet.'}</Text>}
      <Button label={coach ? 'Add something to remember' : 'Add a preference'} onPress={actions.add} />
      {coach ? (
        <List>
          <Pressable testID="memory-clear" accessibilityRole="button" accessibilityLabel="Clear memory. Plan, runs and conversation stay" onPress={actions.clear}
            style={{ minHeight: 60, justifyContent: 'center', paddingHorizontal: 14, paddingVertical: 8 }}>
            <Text variant="body" tone="brandText">Clear memory</Text>
            <Text variant="footnote" tone="muted">Plan, runs and conversation stay</Text>
          </Pressable>
        </List>
      ) : null}
      <Text variant="footnote" tone="muted">{MEDICAL}</Text>
    </ScrollView>
  );
}

export function MemoryEditSheet({ item, coach, defaultKind, onSave, onDelete, onCancel }: {
  item: MemoryItem | null;
  coach: boolean;
  defaultKind?: MemoryKind;
  onSave: (kind: MemoryKind, text: string) => void;
  onDelete: (() => void) | null;
  onCancel: () => void;
}) {
  const { colors } = useTheme();
  const [kind, setKind] = useState<MemoryKind>(item?.kind ?? defaultKind ?? 'note');
  const [text, setText] = useState(item?.text ?? '');
  const label = kindLabel(kind, coach) ?? 'Note';
  return (
    <ScrollView testID="memory-edit" style={{ backgroundColor: colors.surface }} contentContainerStyle={{ ...READABLE, padding: 20, gap: 12 }} keyboardShouldPersistTaps="handled">
      <Text variant="title" accessibilityRole="header">{item ? `Edit ${label.toLowerCase()}` : coach ? 'Add something to remember' : 'Add a preference'}</Text>
      {!item ? (
        <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {kindsFor(coach).map((k) => {
            const on = k === kind;
            return (
              <Pressable key={k} testID={`kind-${k}`} accessibilityRole="radio" accessibilityState={{ checked: on }} onPress={() => setKind(k)}
                style={{ minHeight: MIN_TOUCH, justifyContent: 'center' }}>
                <View style={{ minHeight: 34, paddingHorizontal: 12, justifyContent: 'center', borderRadius: 999, borderWidth: 1,
                  borderColor: on ? colors.text : colors.muted, backgroundColor: on ? colors.sunken : 'transparent' }}>
                  <Text variant="subhead" style={{ fontWeight: on ? '600' : '400' }}>{kindLabel(k, coach)}</Text>
                </View>
              </Pressable>
            );
          })}
        </View>
      ) : null}
      <TextInput testID="memory-text" value={text} onChangeText={setText} multiline accessibilityLabel={label}
        placeholder={kind === 'travel' ? '1 to 3 Dec · treadmill only' : kind === 'injury' ? 'Left achilles · 12 Nov' : 'In your words'}
        placeholderTextColor={colors.muted}
        style={{ minHeight: 88, padding: 12, borderRadius: 12, backgroundColor: colors.sunken, color: colors.text, fontSize: 16, textAlignVertical: 'top' }} />
      {kind === 'injury' ? <Text variant="footnote" tone="muted">{MEDICAL}</Text> : null}
      <Button label="Save" kind="primary" disabled={!text.trim()} onPress={() => onSave(kind, text)} />
      {onDelete ? <Button label="Delete" onPress={onDelete} /> : null}
      <Button label="Cancel" onPress={onCancel} />
    </ScrollView>
  );
}

export function MemoryClearSheet({ onClear, onKeep }: { onClear: () => void; onKeep: () => void }) {
  return (
    <View testID="memory-clear-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">Clear memory?</Text>
      <Text variant="body" tone="muted">Everything the Coach keeps in mind goes. Your plan, runs and the conversation stay.</Text>
      <Button label="Clear memory" kind="primary" onPress={onClear} />
      <Button label="Keep it" onPress={onKeep} />
    </View>
  );
}

/** You's Coach section: the Coach's own switch (only when the server offers the Coach) and the memory or preferences row. */
export function CoachSection({ available, coach, onToggle, onOpen }: {
  available: boolean; coach: boolean; onToggle: (on: boolean) => void; onOpen: () => void;
}) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: 10 }}>
      <Text variant="kicker" accessibilityRole="header" tone="muted" style={{ marginTop: 6 }}>{available ? 'Coach' : 'Training'}</Text>
      <List>
        {available ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 8, minHeight: 60 }}>
            <View style={{ flex: 1 }}>
              <Text variant="body">Coach</Text>
              <Text variant="footnote" tone="muted">Answers questions about your plan and runs. AI, run on Daash’s server. Off hides it everywhere; no number changes.</Text>
            </View>
            <Switch testID="coach-switch" value={coach} onValueChange={onToggle} accessibilityLabel="Coach"
              trackColor={{ false: colors.button, true: colors.success }} thumbColor={colors.surface} />
          </View>
        ) : null}
        <Pressable testID="row-memory" accessibilityRole="button" onPress={onOpen}
          accessibilityLabel={coach ? 'Coach memory. What the Coach keeps in mind' : 'Training preferences. What you’ve told Daash'}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, paddingVertical: 8, minHeight: 60,
            borderTopWidth: available ? 1 : 0, borderTopColor: colors.hairline }}>
          <View style={{ flex: 1 }}>
            <Text variant="body">{coach ? 'Coach memory' : 'Training preferences'}</Text>
            <Text variant="footnote" tone="muted">{coach ? 'What the Coach keeps in mind' : 'What you’ve told Daash'}</Text>
          </View>
          <Icon name="chevronRight" tone="muted" />
        </Pressable>
      </List>
    </View>
  );
}
