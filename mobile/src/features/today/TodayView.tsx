// Today, drawn from its view model. Pure: no data fetching, no navigation, so the
// tests render it on both platforms, in both themes, Coach on and off, at 200%.
import { Platform, RefreshControl, ScrollView, View } from 'react-native';

import type { Freshness } from '../../data/freshness';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Text } from '../../ui/Text';
import { kickerText, type SignalKey, type TodayVM } from './model';
import { ReadinessStrip } from './ReadinessStrip';
import { SessionCard } from './SessionCard';
import { WeekStrip } from './WeekStrip';

/** Above this text size the layout switches: signals stack, explanations clamp, buttons stack. */
export const XL_FONT_SCALE = 1.5;

export type TodayActions = {
  signal: (key: SignalKey) => void;
  recommended: () => void;
  options: () => void;
  how: () => void;
  askWhy: () => void;
  openSession: () => void;
  /** A done day in the week strip: its run. */
  openRun?: (id: string) => void;
  refresh: () => void;
};

export function TodayView({ vm, coach, brand, fontScale, fresh, refreshing, error, actions, os = Platform.OS }: {
  vm: TodayVM;
  coach: boolean;
  /** false while a sheet is open over Today */
  brand: boolean;
  fontScale: number;
  fresh: Freshness | null;
  refreshing: boolean;
  error: string | null;
  actions: TodayActions;
  os?: string;
}) {
  const { colors } = useTheme();
  const xl = fontScale >= XL_FONT_SCALE;
  const ios = os === 'ios';
  return (
    <ScrollView
      testID="today"
      contentInsetAdjustmentBehavior="automatic"
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: ios ? 8 : 0, paddingBottom: 24, gap: 10 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={actions.refresh} tintColor={colors.muted} colors={[colors.text]} />}
    >
      <View testID="today-header" style={{ gap: 4 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
          <Text variant="kicker" tone="muted" testID="kicker" style={{ flex: 1 }}>{kickerText(vm.kicker)}</Text>
          {fresh && (ios || !fresh.stale) ? (
            <Text variant="footnote" tone="muted" numeric testID="freshness">{fresh.label}</Text>
          ) : null}
        </View>
        <Text variant={ios ? 'largeTitle' : 'title'} accessibilityRole="header" numeric>{vm.title}</Text>
      </View>
      {error ? (
        <Text variant="footnote" tone="muted" testID="refresh-error">{`Couldn’t refresh just now. ${fresh?.label ?? ''}`.trim()}</Text>
      ) : null}
      <ReadinessStrip readiness={vm.readiness} coach={coach} brand={brand} xl={xl}
        onSignal={actions.signal} onRecommended={actions.recommended} onOptions={actions.options}
        onHow={actions.how} onAskWhy={actions.askWhy} />
      {vm.session ? <SessionCard session={vm.session} onOpen={actions.openSession} />
        : <Card testID="session-card"><Text variant="headline">Rest day</Text>
          <Text variant="footnote" tone="muted">Nothing planned today. Rest counts toward your active week.</Text></Card>}
      <WeekStrip week={vm.week} onRun={actions.openRun} />
    </ScrollView>
  );
}

/** First open with nothing cached yet: the shapes of the cards, never a spinner over content. */
export function TodayEmpty({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  const { colors } = useTheme();
  if (!error) {
    return (
      <View testID="today-loading" accessibilityLabel="Loading today" style={{ flex: 1, backgroundColor: colors.bg, padding: 16, gap: 10 }}>
        {[140, 170, 110].map((h, i) => <View key={i} style={{ height: h, borderRadius: 12, backgroundColor: colors.surface }} />)}
      </View>
    );
  }
  return (
    <View testID="today-error" style={{ flex: 1, backgroundColor: colors.bg, padding: 16, gap: 12, justifyContent: 'center' }}>
      <Text variant="headline">Couldn{'’'}t load today</Text>
      <Text variant="body" tone="muted">{error}</Text>
      <Button label="Try again" onPress={onRetry} />
    </View>
  );
}
