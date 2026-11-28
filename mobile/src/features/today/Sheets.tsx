// Today's two sheets (build prompt, sections 5.1 and 5.2), drawn from the view model.
import { Pressable, ScrollView, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Text } from '../../ui/Text';
import type { Signal, TodayOption } from './model';
import { SignalChart } from './SignalChart';

export const SAME_EITHER_WAY = 'Readiness is worked out the same way with or without the Coach. Not medical advice.';
// With the Coach off nothing mentions a Coach (build prompt, section 1).
export const FIXED_RULES = 'Readiness is worked out by fixed rules from your own data. Not medical advice.';
const RULES: { when: string; word: string; tone: 'warning' | 'text2' | 'success' }[] = [
  { when: '2 or 3 signals below usual', word: 'Back off', tone: 'warning' },
  { when: '1 signal below usual', word: 'Hold', tone: 'text2' },
  { when: 'None below, load in range', word: 'Push', tone: 'success' },
];

function SheetBody({ children, testID }: { children: React.ReactNode; testID: string }) {
  const { colors } = useTheme();
  return (
    <ScrollView testID={testID} style={{ backgroundColor: colors.surface }} contentContainerStyle={{ padding: 20, paddingTop: 16, gap: 12 }}>
      {children}
    </ScrollView>
  );
}

export function ReadinessSheet({ signal, onDone, coach = true }: { signal: Signal; onDone: () => void; coach?: boolean }) {
  const { colors, derived } = useTheme();
  const badgeTone = signal.status === 'low' ? 'warning' : signal.status === 'usual' ? 'success' : 'muted';
  return (
    <SheetBody testID="readiness-sheet">
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Text variant="title" numeric accessibilityRole="header">{signal.sheet.headline}</Text>
        <View style={{ borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3, backgroundColor: derived.tint(badgeTone === 'muted' ? 'muted' : badgeTone) }}>
          <Text variant="caption" tone={badgeTone} style={{ fontWeight: '600' }}>{signal.sheet.badge}</Text>
        </View>
      </View>
      <SignalChart signal={signal} />
      <Text variant="footnote" tone="muted">{signal.sheet.explanation}</Text>
      <View accessibilityRole="summary" style={{ backgroundColor: colors.sunken, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 4 }}>
        {RULES.map((r) => (
          <View key={r.word} style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 8, gap: 8 }}>
            <Text variant="subhead" style={{ flex: 1 }}>{r.when}</Text>
            <Text variant="subhead" tone={r.tone} style={{ fontWeight: '600' }}>{r.word}</Text>
          </View>
        ))}
      </View>
      <Text variant="footnote" tone="muted">{coach ? SAME_EITHER_WAY : FIXED_RULES}</Text>
      <Button label="Done" onPress={onDone} />
    </SheetBody>
  );
}

export function OptionsSheet({ options, onChoose, onCancel }: {
  options: TodayOption[]; onChoose: (o: TodayOption) => void; onCancel: () => void;
}) {
  const { colors } = useTheme();
  return (
    <SheetBody testID="options-sheet">
      <Text variant="title" accessibilityRole="header">Other options for today</Text>
      <View style={{ backgroundColor: colors.surface, borderColor: colors.hairline, borderWidth: 1, borderRadius: 12, overflow: 'hidden' }}>
        {options.map((o, i) => (
          <Pressable key={o.kind} accessibilityRole="button" accessibilityLabel={`${o.title}. ${o.sub}`} testID={`option-${o.kind}`}
            onPress={() => onChoose(o)}
            style={({ pressed }) => ({ minHeight: Math.max(56, MIN_TOUCH), paddingHorizontal: 14, paddingVertical: 12,
              justifyContent: 'center', borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline,
              backgroundColor: pressed ? colors.sunken : 'transparent' })}>
            <Text variant="headline">{o.title}</Text>
            <Text variant="subhead" tone="muted" numeric>{o.sub}</Text>
          </Pressable>
        ))}
      </View>
      <Text variant="footnote" tone="muted">Your call. Any change to the watch is confirmed first.</Text>
      <Button label="Cancel" onPress={onCancel} />
    </SheetBody>
  );
}
