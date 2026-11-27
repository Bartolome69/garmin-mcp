// The workout builder (build prompt, section 5.7): type a session the way you'd say
// it, see exactly how Daash read it, adjust, pick a day, Save and send. Nothing
// reaches the watch from here: Save and send opens the watch confirm.
//
// Never guesses silently: anything the parser can't read is shown in place, with
// its fix-it. A "Did you mean …?" fix-it is a button that makes that change.
import { useState } from 'react';
import { Pressable, ScrollView, TextInput, View } from 'react-native';

import type { Parsed, Problem } from '../../domain/parser';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { TypeChip } from '../../ui/session/TypeChip';
import { Text } from '../../ui/Text';
import { MAX_STEP } from '../plan/SessionDetailView';
import { StepsCard } from '../plan/StepsCard';
import type { Basis } from '../plan/model';
import { EditSteps, type EditState } from './EditSteps';
import type { Built, DayChoice } from './model';

export const HINT = 'Type it how you’d say it. Daash turns it into steps.';
export const PLACEHOLDER = '5x1km at threshold, 2 min easy';

/** The text a "Did you mean "…"?" fix-it would put in place of the problem, if it names one. */
export function fixFor(p: Problem): string | null {
  const m = /^Did you mean "([^"]+)"\?$/.exec(p.suggestion ?? '');
  return m ? m[1] : null;
}

export function applyFix(text: string, p: Problem): string {
  const fix = fixFor(p);
  return fix == null ? text : text.slice(0, p.start) + fix + text.slice(p.end);
}

function Problems({ text, problems, onFix }: { text: string; problems: Problem[]; onFix: (p: Problem) => void }) {
  const { colors } = useTheme();
  // The text again, with what didn't read marked: underlined and bold, never colour alone.
  const marked: { s: string; bad: boolean }[] = [];
  let at = 0;
  for (const p of [...problems].sort((a, b) => a.start - b.start)) {
    if (p.start < at) continue;
    if (p.start > at) marked.push({ s: text.slice(at, p.start), bad: false });
    marked.push({ s: text.slice(p.start, p.end), bad: true });
    at = p.end;
  }
  if (at < text.length) marked.push({ s: text.slice(at), bad: false });
  return (
    <View testID="problems" style={{ gap: 8 }}>
      <Text variant="subhead" tone="text2" accessibilityLabel={`Not read: ${problems.map((p) => p.text).join(', ')}`}>
        {marked.map((m, i) => (m.bad
          ? <Text key={i} variant="subhead" style={{ fontWeight: '700', textDecorationLine: 'underline', textDecorationStyle: 'solid' }}>{m.s}</Text>
          : <Text key={i} variant="subhead" tone="muted">{m.s}</Text>))}
      </Text>
      {problems.map((p, i) => {
        const fix = fixFor(p);
        return (
          <View key={i} testID={`problem-${i}`} accessible={!fix} accessibilityLabel={`${p.message}. ${p.suggestion ?? ''}`}
            style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start' }}>
            <Icon name="warning" tone="text2" />
            <View style={{ flex: 1, gap: 4 }}>
              <Text variant="footnote" style={{ fontWeight: '600' }}>{p.message}</Text>
              {fix ? (
                <Pressable testID={`fix-${i}`} accessibilityRole="button" accessibilityLabel={`${p.message}. Change it to ${fix}`}
                  onPress={() => onFix(p)} style={{ minHeight: 44, justifyContent: 'center', alignSelf: 'flex-start' }}>
                  <View style={{ minHeight: 32, paddingHorizontal: 12, borderRadius: 999, justifyContent: 'center', borderWidth: 1, borderColor: colors.muted }}>
                    <Text variant="subhead" style={{ fontWeight: '600' }}>{`Use “${fix}”`}</Text>
                  </View>
                </Pressable>
              ) : p.suggestion ? <Text variant="footnote" tone="muted">{p.suggestion}</Text> : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}

function Chip({ label, on, onPress, testID, hint }: { label: string; on: boolean; onPress: () => void; testID: string; hint?: string }) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="radio" accessibilityState={{ selected: on }} accessibilityHint={hint}
      onPress={onPress} style={{ minHeight: 44, justifyContent: 'center' }}>
      <View style={{ minHeight: 32, paddingHorizontal: 14, borderRadius: 999, justifyContent: 'center',
        backgroundColor: on ? colors.text : 'transparent', borderWidth: 1, borderColor: on ? colors.text : colors.muted }}>
        <Text variant="subhead" tone={on ? 'bg' : 'text'} style={{ fontWeight: '600' }} numeric>{label}</Text>
      </View>
    </Pressable>
  );
}

export function BuilderView({ text, onText, parsed, built, basis, step, onStep, adjust, rule, coach, coachNote, days, day, onDay,
  edit, onEditOpen, onEdit, onEditDone, onSave, brand = true, editing = false }: {
  text: string;
  onText: (t: string) => void;
  parsed: Parsed;
  built: Built | null;
  basis: Basis | null;
  step: number;
  onStep: (n: number) => void;
  adjust: string;                // "Threshold from your 10K of 43:30"
  rule: string | null;           // the back-to-back check on the chosen day
  coach: boolean;
  coachNote: string | null;      // Coach on only, from the server
  days: DayChoice[];
  day: string;
  onDay: (d: string) => void;
  /** Edit steps: the rows, or null when closed. */
  edit: EditState | null;
  onEditOpen: () => void;
  onEdit: (e: EditState) => void;
  onEditDone: () => void;
  onSave: () => void;
  brand?: boolean;
  /** Editing a planned session: saving replaces it. */
  editing?: boolean;
}) {
  const { colors } = useTheme();
  const [picking, setPicking] = useState(false);
  const chosen = days.find((d) => d.date === day);
  const shown = picking ? days : days.filter((d) => d.date === day);
  const empty = !text.trim();
  return (
    <ScrollView testID="builder" contentInsetAdjustmentBehavior="automatic" keyboardShouldPersistTaps="handled"
      style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={{ padding: 16, gap: 12 }}>
      <Text variant="footnote" tone="muted">{HINT}</Text>
      <TextInput testID="builder-input" accessibilityLabel="Workout" accessibilityHint={HINT}
        value={text} onChangeText={onText} editable={!edit} placeholder={PLACEHOLDER} placeholderTextColor={colors.muted}
        multiline autoCapitalize="none" autoCorrect={false} spellCheck={false}
        style={{ minHeight: 56, paddingHorizontal: 14, paddingVertical: 12, borderRadius: 12, backgroundColor: colors.sunken,
          color: colors.text, fontSize: 17, lineHeight: 22, textAlignVertical: 'top' }} />

      {parsed.problems.length ? <Problems text={text} problems={parsed.problems} onFix={(p) => onText(applyFix(text, p))} /> : null}

      {parsed.ok ? (
        <View testID="read-as-row" accessible accessibilityLiveRegion="polite" style={{ flexDirection: 'row', gap: 6, alignItems: 'flex-start' }}>
          <Icon name="check" tone="text2" />
          <Text testID="read-as" variant="footnote" tone="text2" style={{ flex: 1 }} numeric>
            {parsed.readAs}{coach ? '' : <Text variant="footnote" tone="muted">{' · parsed on your phone'}</Text>}
          </Text>
        </View>
      ) : null}

      {built && !edit ? (
        <StepsCard testID="builder-steps" steps={built.detail.steps} type={built.type} basis={basis} step={0} header={(
          <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <TypeChip type={built.type} label={built.typeLabel} />
            <Text variant="footnote" tone="muted" numeric testID="estimate">{built.estimate}</Text>
          </View>
        )} />
      ) : null}

      {edit ? <EditSteps state={edit} onChange={onEdit} parsed={parsed} /> : null}

      {built?.detail.adjustable && !edit ? (
        <View testID="adjust" style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 8, paddingLeft: 12, borderRadius: 12,
          backgroundColor: colors.sunken, flexWrap: 'wrap' }}>
          <Text variant="footnote" style={{ flex: 1, minWidth: 160 }} numeric>{adjust}</Text>
          <View style={{ flexDirection: 'row', gap: 6 }}>
            <Button label="Easier" small disabled={step <= -MAX_STEP} onPress={() => onStep(step - 1)} />
            <Button label="Harder" small disabled={step >= MAX_STEP} onPress={() => onStep(step + 1)} />
          </View>
        </View>
      ) : null}

      {coach && coachNote && built && !edit ? (
        <View testID="coach-note" style={{ gap: 6, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
          <Text variant="kicker" tone="muted">Coach</Text>
          <Text variant="coach">{coachNote}</Text>
        </View>
      ) : null}

      {built ? (
        <View style={{ gap: 4 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Text variant="subhead" style={{ fontWeight: '600' }}>Day</Text>
            <View accessibilityRole="radiogroup" accessibilityLabel="Day" style={{ flex: 1, flexDirection: 'row', flexWrap: 'wrap', columnGap: 8 }}>
              {shown.map((d) => (
                <Chip key={d.date} testID={`day-${d.date}`} label={picking ? d.label : d.long} on={d.date === day}
                  hint={d.planned ? `${d.planned} is planned that day` : undefined}
                  onPress={() => { onDay(d.date); setPicking(false); }} />
              ))}
              {!picking ? <Chip testID="day-pick" label="Pick" on={false} onPress={() => setPicking(true)} /> : null}
            </View>
          </View>
          {chosen?.planned && !(editing && chosen.date === day && chosen.planned === built.title) ? (
            <Text testID="day-planned" variant="footnote" tone="muted">{`${chosen.planned} is already planned that day. This adds a second session.`}</Text>
          ) : null}
          {rule ? <Text testID="rule" variant="footnote" tone="muted">{rule}</Text> : null}
        </View>
      ) : null}

      {empty ? null : (
        <View style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
          <Button label={edit ? 'Done' : 'Edit steps'} full disabled={!built && !edit} onPress={edit ? onEditDone : onEditOpen} />
          <Button label="Save and send" kind="primary" brand={brand} full disabled={!built || !!edit} onPress={onSave} />
        </View>
      )}
    </ScrollView>
  );
}
