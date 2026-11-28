// A session's steps (build prompt, section 5.6): one line per step, repeats grouped
// in a "10 ×" block, the target range per km (or heart rate / power). Drawn the same
// on session detail and in the builder (section 5.7).
import type { ReactNode } from 'react';
import { View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Card } from '../../ui/Card';
import { TYPE_TOKEN } from '../../ui/session/types';
import { Text } from '../../ui/Text';
import type { SessionType } from '../today/model';
import { mmss } from '../../domain/format';
import { paces } from '../../domain/paces';
import type { Basis, StepItem, StepRow, Target } from './model';

/** The words for a target, re-stepped Easier (−) or Harder (+) when it's a pace from the basis race. */
export function targetWords(t: Target, basis: Basis | null, step: number): string {
  switch (t.kind) {
    case 'pace': {
      const range = step && t.zone && basis
        ? paces(basis.raceKm, basis.raceSeconds, { raceDate: basis.raceDate, step }).zones[t.zone]
        : t.range;
      return `${mmss(range[0])}–${mmss(range[1])}`;
    }
    case 'hr': return `${t.range[0]}–${t.range[1]} bpm`;
    case 'power': return `${t.range[0]}–${t.range[1]} W`;
    case 'easy': return 'easy';
    default: return '';
  }
}

function Step({ row, type, basis, step, first }: { row: StepRow; type: SessionType; basis: Basis | null; step: number; first: boolean }) {
  const { colors } = useTheme();
  const target = targetWords(row.target, basis, step);
  return (
    <View accessible accessibilityLabel={`${row.name}, ${row.detail}${target ? `, ${target.replace('–', ' to ')}${row.target.kind === 'pace' ? ' per km' : ''}` : ''}`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 40, paddingVertical: 6, borderTopWidth: first ? 0 : 1, borderTopColor: colors.hairline }}>
      <View style={{ width: 6, height: 26, borderRadius: 3,
        backgroundColor: row.kind === 'work' ? colors[TYPE_TOKEN[type]] : colors.button }} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="subhead" style={{ fontWeight: '600' }} numeric>{row.name}</Text>
        <Text variant="footnote" tone="muted">{row.detail}</Text>
      </View>
      {target ? <Text variant="stepTarget" numeric>{target}</Text> : null}
    </View>
  );
}

/** header replaces the "Steps" kicker (the builder puts the type chip and estimate there). */
export function StepsCard({ steps, type, basis, step, header, testID = 'steps' }: {
  steps: StepItem[]; type: SessionType; basis: Basis | null; step: number; header?: ReactNode; testID?: string;
}) {
  const { colors } = useTheme();
  const unit = steps.some((s) => (s.kind === 'repeat' ? s.steps : [s]).some((r) => r.target.kind === 'pace')) ? '/km' : '';
  return (
    <Card testID={testID} style={{ paddingVertical: 10, gap: 2 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingBottom: 6 }}>
        {header ?? <Text variant="kicker" accessibilityRole="header" tone="muted">Steps</Text>}
        {unit && !header ? <Text variant="kicker" tone="muted" style={{ textTransform: 'none' }}>{unit}</Text> : null}
      </View>
      {steps.map((s, i) => (s.kind === 'repeat' ? (
        <View key={i} testID="repeat" accessible={false}
          style={{ borderWidth: 1, borderColor: colors.hairline, borderRadius: 12, paddingHorizontal: 10, paddingTop: 6, marginVertical: 6 }}>
          <View style={{ position: 'absolute', top: -10, left: 10, paddingHorizontal: 6, backgroundColor: colors.surface }}>
            <Text variant="kicker" accessibilityLabel={`Repeat ${s.times} times`} numeric style={{ fontWeight: '700' }}>{`${s.times} ×`}</Text>
          </View>
          {s.steps.map((r, j) => <Step key={j} row={r} type={type} basis={basis} step={step} first={j === 0} />)}
        </View>
      ) : <Step key={i} row={s} type={type} basis={basis} step={step} first={i === 0} />))}
    </Card>
  );
}
