// Session detail (build prompt, section 5.6): a 10-rep session fits without
// scrolling. One line per step, repeats grouped in a "10 ×" block, the target pace
// range per km (or heart rate / power), "Based on your 10K of 43:30 (12 Oct)" with
// Easier / Harder (the shared pace engine, 2% a step, the server's twin), the watch
// status row, and Move / Swap / Skip. Changing targets or the day reaches the watch
// only through the confirm sheet (milestone 5).
import { ScrollView, View } from 'react-native';

import { mmss } from '../../domain/format';
import { paces } from '../../domain/paces';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { StructureBar } from '../../ui/session/StructureBar';
import { TypeChip } from '../../ui/session/TypeChip';
import { TYPE_TOKEN } from '../../ui/session/types';
import { WatchStatus } from '../../ui/session/WatchStatus';
import { Text } from '../../ui/Text';
import type { Basis, PlanSession, StepRow, Target } from './model';

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

export function stepLabel(step: number): string | null {
  if (!step) return null;
  const n = Math.abs(step);
  return `${step < 0 ? 'Easier' : 'Harder'} by ${2 * n}%`;
}

const MAX_STEP = 3;

function Step({ row, session, basis, step, first }: { row: StepRow; session: PlanSession; basis: Basis | null; step: number; first: boolean }) {
  const { colors } = useTheme();
  const target = targetWords(row.target, basis, step);
  return (
    <View accessible accessibilityLabel={`${row.name}, ${row.detail}${target ? `, ${target.replace('–', ' to ')}${row.target.kind === 'pace' ? ' per km' : ''}` : ''}`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 40, paddingVertical: 6, borderTopWidth: first ? 0 : 1, borderTopColor: colors.hairline }}>
      <View style={{ width: 6, height: 26, borderRadius: 3,
        backgroundColor: row.kind === 'work' ? colors[TYPE_TOKEN[session.type]] : colors.button }} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="subhead" style={{ fontWeight: '600' }} numeric>{row.name}</Text>
        <Text variant="footnote" tone="muted">{row.detail}</Text>
      </View>
      {target ? <Text variant="stepTarget" numeric>{target}</Text> : null}
    </View>
  );
}

export function SessionDetailView({ session, basis, coach, step, onStep, actions }: {
  session: PlanSession;
  basis: Basis | null;
  coach: boolean;
  step: number;
  onStep: (step: number) => void;
  actions: { send: () => void; move: () => void; swap: () => void; skip: () => void };
}) {
  const { colors } = useTheme();
  const d = session.detail;
  const unit = d?.steps.some((s) => (s.kind === 'repeat' ? s.steps : [s]).some((r) => r.target.kind === 'pace')) ? '/km' : '';
  const adjusted = stepLabel(step);
  const watch = session.watch;
  const onWatch = watch?.state === 'on_watch' || watch?.state === 'changed_pending_sync';
  return (
    <ScrollView testID="session" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 12 }}>
      <View style={{ gap: 6 }}>
        <TypeChip type={session.type} label={session.typeLabel} />
        <Text variant="title" accessibilityRole="header" numeric>{session.title}</Text>
        <Text variant="footnote" tone="muted" numeric>{d?.meta ?? session.sub}</Text>
      </View>
      {d ? <StructureBar parts={d.structure} type={session.type} legend={d.legend} /> : null}
      {d ? (
        <Card testID="steps" style={{ paddingVertical: 10, gap: 2 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingBottom: 6 }}>
            <Text variant="kicker" tone="muted">Steps</Text>
            {unit ? <Text variant="kicker" tone="muted" style={{ textTransform: 'none' }}>{unit}</Text> : null}
          </View>
          {d.steps.map((s, i) => (s.kind === 'repeat' ? (
            <View key={i} testID="repeat" accessible={false}
              style={{ borderWidth: 1, borderColor: colors.hairline, borderRadius: 12, paddingHorizontal: 10, paddingTop: 6, marginVertical: 6 }}>
              <View style={{ position: 'absolute', top: -10, left: 10, paddingHorizontal: 6, backgroundColor: colors.surface }}>
                <Text variant="kicker" accessibilityLabel={`Repeat ${s.times} times`} numeric style={{ fontWeight: '700' }}>{`${s.times} ×`}</Text>
              </View>
              {s.steps.map((r, j) => <Step key={j} row={r} session={session} basis={basis} step={step} first={j === 0} />)}
            </View>
          ) : <Step key={i} row={s} session={session} basis={basis} step={step} first={i === 0} />))}
        </Card>
      ) : null}
      {d?.adjustable && basis ? (
        <View testID="adjust" style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 8, paddingLeft: 12, borderRadius: 12,
          backgroundColor: colors.sunken, flexWrap: 'wrap' }}>
          <Text variant="footnote" style={{ flex: 1, minWidth: 160 }} numeric>
            {paces(basis.raceKm, basis.raceSeconds, { raceDate: basis.raceDate }).basis}{adjusted ? ` · ${adjusted}` : ''}
          </Text>
          <View style={{ flexDirection: 'row', gap: 6 }}>
            <Button label="Easier" small disabled={step <= -MAX_STEP} onPress={() => onStep(step - 1)} />
            <Button label="Harder" small disabled={step >= MAX_STEP} onPress={() => onStep(step + 1)} />
          </View>
        </View>
      ) : null}
      {coach && d?.coachNote ? (
        <View testID="coach-note" style={{ gap: 6, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
          <Text variant="kicker" tone="muted">Coach · why this session</Text>
          <Text variant="coach">{d.coachNote}</Text>
        </View>
      ) : null}
      {watch ? (
        <Card style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <View style={{ flexShrink: 1, gap: 2 }}>
            <WatchStatus state={watch.state} at={watch.at} />
            {adjusted && onWatch ? <Text variant="footnote" tone="muted">The watch has the old targets until you send.</Text> : null}
          </View>
          {!onWatch || adjusted ? <Button label={adjusted && onWatch ? 'Send changes' : 'Send to watch'} kind="emphasis" small onPress={actions.send} /> : null}
        </Card>
      ) : null}
      {!session.done && session.type !== 'rest' ? (
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Button label="Move" small full onPress={actions.move} />
          <Button label="Swap" small full onPress={actions.swap} />
          <Button label="Skip" small full onPress={actions.skip} />
        </View>
      ) : null}
    </ScrollView>
  );
}
