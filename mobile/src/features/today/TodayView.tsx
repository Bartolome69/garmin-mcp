// Today, drawn from its view model. Pure: no data fetching, no navigation, so the
// tests render it on both platforms, in both themes, Coach on and off, at 200%.
import { memo } from 'react';
import { Platform, RefreshControl, ScrollView, View } from 'react-native';

import type { Freshness } from '../../data/freshness';
import type { FailureKind } from '../../data/resource';
import { useTheme } from '../../theme/ThemeProvider';
import { useRenderCount } from '../../ui/hooks';
import { FailureBanner, LoadState, SampleDataLabel } from '../../ui/StateViews';
import { Card } from '../../ui/Card';
import { Text } from '../../ui/Text';
import { kickerText, type SignalKey, type TodayVM } from './model';
import { ReadinessStrip } from './ReadinessStrip';
import { SessionCard } from './SessionCard';
import { WeekStrip } from './WeekStrip';
import { READABLE } from '../../ui/Screen';

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

// Memoised: the screen keeps its props the same while nothing Today shows changed (a watch-queue
// tick for another day's session, a refresh that brought the same day), so it doesn't redraw.
export const TodayView = memo(function TodayView({ vm, coach, brand, fontScale, fresh, refreshing, error, actions, sample = false, failure = null, os = Platform.OS }: {
  vm: TodayVM;
  coach: boolean;
  /** false while a sheet is open over Today */
  brand: boolean;
  fontScale: number;
  fresh: Freshness | null;
  refreshing: boolean;
  error: string | null;
  actions: TodayActions;
  /** Sample data is showing (data/sample.ts): the label sits in the kicker row, where the
   *  time it was fetched would go, so it adds no height above the fold. */
  sample?: boolean;
  /** Garmin sign-in lapsed or signed out: the banner, first thing inside the scroll view. */
  failure?: FailureKind | null;
  os?: string;
}) {
  const { colors } = useTheme();
  useRenderCount('TodayView');
  const xl = fontScale >= XL_FONT_SCALE;
  const ios = os === 'ios';
  return (
    <ScrollView
      testID="today"
      contentInsetAdjustmentBehavior="automatic"
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, paddingHorizontal: 16, paddingTop: ios ? 8 : 0, paddingBottom: 24, gap: 10 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={actions.refresh} tintColor={colors.muted} colors={[colors.text]} />}
    >
      {/* Inside the scroll view, so on iOS the safe area insets it with the rest of the screen. */}
      <FailureBanner failure={failure} style={{ marginHorizontal: 0, marginTop: 0 }} />
      <View testID="today-header" style={{ gap: 4 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
          <Text variant="kicker" tone="muted" testID="kicker" style={{ flex: 1 }}>{kickerText(vm.kicker)}</Text>
          {sample ? <SampleDataLabel inline /> : fresh && (ios || !fresh.stale) ? (
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
});

/** First open with nothing cached yet: the shapes of the cards, never a spinner over content. */
/** No day to draw yet: a skeleton while it loads, or why not (offline, Garmin sign-in, signed out, the server). */
export function TodayEmpty({ error, onRetry, failure = null, what = 'today', testID = 'today' }: {
  error: string | null; onRetry: () => void; failure?: FailureKind | null; what?: string; testID?: string;
}) {
  return <LoadState what={what} failure={failure} error={error} onRetry={onRetry} testID={testID} />;
}
