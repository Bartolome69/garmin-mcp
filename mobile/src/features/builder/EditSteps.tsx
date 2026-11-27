// Edit steps (build prompt, section 5.7): one row per step, written as text the
// parser reads, so it is the same reader with the same fix-its. The rows become the
// builder's text; the added warm-up and cool-down can be taken off or put back.
import { Pressable, TextInput, View } from 'react-native';

import { parseWorkout, stepWords, type Parsed } from '../../domain/parser';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';

export type EditState = { rows: string[]; add: { warmup: boolean; cooldown: boolean } };

/** The builder's text from the rows: empty rows drop out. */
export const rowsText = (rows: string[]) => rows.map((r) => r.trim()).filter(Boolean).join(', ');

function Added({ which, on, words, onToggle }: { which: 'warmup' | 'cooldown'; on: boolean; words: string | null; onToggle: () => void }) {
  const name = which === 'warmup' ? 'warm-up' : 'cool-down';
  return (
    <View testID={`added-${which}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 44 }}>
      <Text variant="subhead" tone={on ? 'text' : 'muted'} style={{ flex: 1 }} numeric>{on ? words ?? `10 min ${name} (added)` : `No ${name}`}</Text>
      <Pressable testID={`toggle-${which}`} accessibilityRole="button" accessibilityLabel={on ? `Take off the added ${name}` : `Add a ${name}`}
        onPress={onToggle} style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: 4 }}>
        <Text variant="subhead" style={{ fontWeight: '600', textDecorationLine: 'underline' }}>{on ? 'Take off' : `Add a ${name}`}</Text>
      </Pressable>
    </View>
  );
}

export function EditSteps({ state, onChange, parsed }: { state: EditState; onChange: (e: EditState) => void; parsed: Parsed }) {
  const { colors } = useTheme();
  const set = (rows: string[]) => onChange({ ...state, rows });
  const added = (k: 'warmup' | 'cooldown') => parsed.steps.find((s) => s.kind === k && s.added);
  const quality = parsed.steps.some((s) => 'added' in s && s.added) || !state.add.warmup || !state.add.cooldown;
  const written = (k: 'warmup' | 'cooldown') => parsed.steps.some((s) => s.kind === k && !s.added);
  return (
    <Card testID="edit-steps" style={{ paddingVertical: 10, gap: 4 }}>
      <Text variant="kicker" tone="muted">Steps</Text>
      {quality && !written('warmup') ? (
        <Added which="warmup" on={state.add.warmup} words={added('warmup') ? stepWords(added('warmup')!) : null}
          onToggle={() => onChange({ ...state, add: { ...state.add, warmup: !state.add.warmup } })} />
      ) : null}
      {state.rows.map((row, i) => {
        const problem = row.trim() ? parseWorkout(row, { add: { warmup: false, cooldown: false } }).problems[0] : undefined;
        return (
          <View key={i} style={{ gap: 4 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <TextInput testID={`row-${i}`} accessibilityLabel={`Step ${i + 1}`} value={row}
                onChangeText={(t) => set(state.rows.map((r, j) => (j === i ? t : r)))}
                multiline scrollEnabled={false} blurOnSubmit
                autoCapitalize="none" autoCorrect={false} spellCheck={false} placeholder="2 min easy" placeholderTextColor={colors.muted}
                style={{ flex: 1, minHeight: 44, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: colors.sunken,
                  color: colors.text, fontSize: 16, lineHeight: 21, textAlignVertical: 'center' }} />
              <Pressable testID={`remove-${i}`} accessibilityRole="button" accessibilityLabel={`Remove step ${i + 1}`}
                onPress={() => set(state.rows.filter((_, j) => j !== i))}
                style={{ minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' }}>
                <Icon name="close" tone="muted" />
              </Pressable>
            </View>
            {problem ? (
              <Text testID={`row-problem-${i}`} variant="footnote" tone="text2">{`${problem.message}${problem.suggestion ? `. ${problem.suggestion}` : ''}`}</Text>
            ) : null}
          </View>
        );
      })}
      {quality && !written('cooldown') ? (
        <Added which="cooldown" on={state.add.cooldown} words={added('cooldown') ? stepWords(added('cooldown')!) : null}
          onToggle={() => onChange({ ...state, add: { ...state.add, cooldown: !state.add.cooldown } })} />
      ) : null}
      <View style={{ paddingTop: 6 }}>
        <Button label="Add a step" small icon="add" onPress={() => set([...state.rows, ''])} />
      </View>
    </Card>
  );
}
