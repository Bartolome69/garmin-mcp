// The Plan tab: Week (default), Block and Trends under the platform's switch, the
// freshness label when the copy is old, and on Android the "New workout" FAB on
// Week. Offline first: the cached plan draws at once; pull to refresh.
import { useState } from 'react';
import { Platform, RefreshControl, ScrollView, View } from 'react-native';

import type { Freshness } from '../../data/freshness';
import { useTheme } from '../../theme/ThemeProvider';
import { Fab } from '../../ui/Fab';
import { Text } from '../../ui/Text';
import { BlockView } from './BlockView';
import type { PlanVM } from './model';
import type { Pending } from './PlanProvider';
import { PlanTabs, type PlanTab } from './PlanTabs';
import { TrendsView } from './TrendsView';
import { WeekView, type WeekActions } from './WeekView';

export type PlanActions = WeekActions & { refresh: () => void; trim: () => void; plus: () => void; change: () => void };

export function PlanView({ vm, tab, onTab, index, onIndex, pending, proposalText, plus, brand, fresh, refreshing, width, os = Platform.OS, actions }: {
  vm: PlanVM;
  tab: PlanTab;
  onTab: (t: PlanTab) => void;
  index: number;
  onIndex: (i: number) => void;
  pending: Pending | null;
  proposalText: string | null;
  plus: boolean;
  /** false while a sheet is open over Plan: the FAB loses its brand fill. */
  brand: boolean;
  fresh: Freshness | null;
  refreshing: boolean;
  width: number;
  os?: string;
  actions: PlanActions;
}) {
  const { colors } = useTheme();
  const [dragging, setDragging] = useState(false);
  const tabs = <PlanTabs value={tab} onChange={onTab} os={os} />;
  return (
    <View testID="plan" style={{ flex: 1, backgroundColor: colors.bg }}>
      {os !== 'ios' ? tabs : null}
      <ScrollView
        testID="plan-scroll"
        contentInsetAdjustmentBehavior="automatic"
        scrollEnabled={!dragging}
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: os !== 'ios' && tab === 'Week' ? 88 : 16 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={actions.refresh} tintColor={colors.muted} />}
      >
        {os === 'ios' ? tabs : null}
        {fresh?.stale ? (
          <Text testID="plan-stale" variant="footnote" tone="muted" style={{ paddingHorizontal: 16, paddingTop: 8 }}>{fresh.label}</Text>
        ) : null}
        <View style={{ paddingTop: 8 }}>
          {tab === 'Week' ? (
            <WeekView vm={vm} index={index} onIndex={onIndex} pending={pending} proposalText={proposalText} width={width} os={os}
              actions={actions} onDragging={setDragging} />
          ) : tab === 'Block' ? (
            <BlockView block={vm.block} onChange={actions.change} />
          ) : (
            <TrendsView vm={vm.trends} plus={plus} onTrim={actions.trim} onPlus={actions.plus} />
          )}
        </View>
      </ScrollView>
      {os !== 'ios' && tab === 'Week' ? <Fab label="New workout" icon="add" brand={brand} onPress={actions.newWorkout} /> : null}
    </View>
  );
}
