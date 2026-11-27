// Session detail (build prompt, section 5.6): a 10-rep session fits without
// scrolling. One line per step, repeats grouped in a "10 ×" block, the target pace
// range per km (or heart rate / power), "Based on your 10K of 43:30 (12 Oct)" with
// Easier / Harder (the shared pace engine, 2% a step, the server's twin), the watch
// status row with every send state (watch/WatchCard.tsx), and Move / Swap / Skip.
// Changing targets or the day reaches the watch only through the confirm sheet.
import { ScrollView, View } from 'react-native';

import { paces } from '../../domain/paces';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { StructureBar } from '../../ui/session/StructureBar';
import { TypeChip } from '../../ui/session/TypeChip';
import { Text } from '../../ui/Text';
import { WatchCard } from '../watch/WatchCard';
import type { Basis, PlanSession } from './model';
import { StepsCard } from './StepsCard';

export { targetWords } from './StepsCard';

export function stepLabel(step: number): string | null {
  if (!step) return null;
  const n = Math.abs(step);
  return `${step < 0 ? 'Easier' : 'Harder'} by ${2 * n}%`;
}

export const MAX_STEP = 3;

export type SessionActions = {
  send: () => void; resend: () => void; remove: () => void; trouble: () => void;
  move: () => void; swap: () => void; skip: () => void;
};

export function SessionDetailView({ session, basis, coach, step, onStep, device = null, error = null, retrying = false, brand = true, actions }: {
  session: PlanSession;
  basis: Basis | null;
  coach: boolean;
  step: number;
  onStep: (step: number) => void;
  /** The watch by name, when Garmin says which: "Forerunner 965". */
  device?: string | null;
  /** The last send's failure in words, from the watch queue. */
  error?: string | null;
  retrying?: boolean;
  /** false while a sheet is open over the screen. */
  brand?: boolean;
  actions: SessionActions;
}) {
  const { colors } = useTheme();
  const d = session.detail;
  const adjusted = stepLabel(step);
  const watch = session.watch;
  return (
    <ScrollView testID="session" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 12 }}>
      <View style={{ gap: 6 }}>
        <TypeChip type={session.type} label={session.typeLabel} />
        <Text variant="title" accessibilityRole="header" numeric>{session.title}</Text>
        <Text variant="footnote" tone="muted" numeric>{d?.meta ?? session.sub}</Text>
      </View>
      {d ? <StructureBar parts={d.structure} type={session.type} legend={d.legend} /> : null}
      {d ? <StepsCard steps={d.steps} type={session.type} basis={basis} step={step} /> : null}
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
        <WatchCard watch={watch} device={device} error={error} retrying={retrying} adjusted={!!adjusted} brand={brand}
          onSend={actions.send} onResend={actions.resend} onRemove={actions.remove} onTrouble={actions.trouble} />
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
