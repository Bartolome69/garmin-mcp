// Plan · Week (build prompt, section 5.3): "Week 4 · Base" with small previous and
// next chevrons and "This week", seven rows of about 56 pt on one screen, a
// horizontal swipe between weeks, hold and drag to move a session (with the
// back-to-back warning before anything changes), "Life happened" and, on iOS,
// "New workout" (Android has the extended FAB instead).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, FlatList, Platform, Pressable, View } from 'react-native';

import { addDays } from '../../domain/format';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import { DayRow } from './DayRow';
import type { PlanSession, PlanVM, PlanWeek } from './model';
import { canMove } from './moves';
import type { Pending } from './PlanProvider';

export type WeekActions = {
  open: (s: PlanSession) => void;
  move: (id: string, to: string) => void;
  moveAnyway: () => void;
  keep: () => void;
  undo: () => void;
  review: () => void;
  life: () => void;
  newWorkout: () => void;
};

type Layouts = Record<string, { y: number; h: number }>;

function WeekPage({ week, today, width, actions, onDragging }: {
  week: PlanWeek; today: string; width: number; actions: WeekActions; onDragging: (on: boolean) => void;
}) {
  const layouts = useRef<Layouts>({});
  const translate = useRef(new Animated.Value(0)).current;
  const [lifted, setLifted] = useState<{ id: string; from: string } | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const targetRef = useRef<string | null>(null);
  // Read by the gesture's callbacks, which outlive the render they were made in.
  const liftedRef = useRef<string | null>(null);

  const dayAt = useCallback((from: string, dy: number): string | null => {
    const start = layouts.current[from];
    if (!start) return null;
    const centre = start.y + dy + start.h / 2;
    const hit = Object.entries(layouts.current).find(([, l]) => centre >= l.y && centre < l.y + l.h);
    return hit ? hit[0] : null;
  }, []);

  const drag = (from: string) => ({
    onStart: (id: string) => { liftedRef.current = id; setLifted({ id, from }); onDragging(true); },
    onMove: (dy: number) => {
      translate.setValue(dy);
      const at = dayAt(from, dy);
      if (at !== targetRef.current) { targetRef.current = at; setTarget(at); }
    },
    onEnd: () => {
      const to = targetRef.current;
      const id = liftedRef.current;
      liftedRef.current = null;
      translate.setValue(0);
      targetRef.current = null;
      setTarget(null);
      setLifted(null);
      onDragging(false);
      if (id && to && to !== from) actions.move(id, to);
    },
  });

  const dayWord = (iso: string) => week.days.find((d) => d.date === iso)?.dow ?? '';
  return (
    <View style={{ width, paddingHorizontal: 16 }} testID={`week-${week.start}`}>
      {week.days.map((day, i) => {
        const rows = day.sessions.length ? day.sessions : [null];
        return (
          <View key={day.date} testID={`day-${day.date}`} onLayout={(e) => { layouts.current[day.date] = { y: e.nativeEvent.layout.y, h: e.nativeEvent.layout.height }; }}>
            {rows.map((s, j) => {
              const movable = !!s && canMove(s, today);
              const isLifted = !!s && lifted?.id === s.id;
              return (
                <DayRow
                  key={s?.id ?? `empty-${day.date}`}
                  day={day}
                  session={s}
                  first={i === 0 && j === 0}
                  onOpen={actions.open}
                  onAction={movable ? (x, by) => actions.move(x.id, addDays(x.date, by)) : undefined}
                  drag={movable ? drag(day.date) : undefined}
                  lifted={isLifted}
                  dropping={isLifted && target ? `dropping on ${dayWord(target).charAt(0)}${dayWord(target).slice(1).toLowerCase()}` : null}
                  target={!!lifted && target === day.date && j === 0}
                  translate={isLifted ? translate : undefined}
                />
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

export function WeekView({ vm, index, onIndex, pending, proposalText, width, os = Platform.OS, brand = true, actions, onDragging }: {
  vm: PlanVM;
  index: number;
  onIndex: (i: number) => void;
  pending: Pending | null;
  /** "Moving tempo from Fri to Thu.", while a move waits for its confirm. */
  proposalText: string | null;
  width: number;
  os?: string;
  /** The banner's main button is the screen's one brand fill (Android's FAB steps aside for it). */
  brand?: boolean;
  actions: WeekActions;
  onDragging: (on: boolean) => void;
}) {
  const { colors } = useTheme();
  const list = useRef<FlatList<PlanWeek>>(null);
  const [dragging, setDragging] = useState(false);
  const current = useMemo(() => vm.weeks.findIndex((w) => w.start === vm.currentWeek), [vm]);
  const week = vm.weeks[index] ?? vm.weeks[0];

  const go = useCallback((i: number) => {
    const to = Math.max(0, Math.min(vm.weeks.length - 1, i));
    onIndex(to);
    list.current?.scrollToIndex({ index: to, animated: true });
  }, [vm.weeks.length, onIndex]);

  useEffect(() => { list.current?.scrollToIndex({ index, animated: false }); }, [width]); // eslint-disable-line react-hooks/exhaustive-deps

  const onViewable = useRef(({ viewableItems }: { viewableItems: { index?: number | null; isViewable: boolean }[] }) => {
    const first = viewableItems.find((v) => v.isViewable);
    if (first?.index != null) onIndexRef.current(first.index);
  }).current;
  const onIndexRef = useRef(onIndex);
  onIndexRef.current = onIndex;

  const setDrag = (on: boolean) => { setDragging(on); onDragging(on); };

  if (!week) return null;
  return (
    <View testID="week" style={{ gap: 8 }}>
      <View style={{ paddingHorizontal: 16 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <Text variant="headline" accessibilityRole="header" testID="week-title" style={{ flexShrink: 1 }}>{week.title}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Pressable testID="week-prev" accessibilityRole="button" accessibilityLabel="Previous week" disabled={index === 0}
              onPress={() => go(index - 1)} style={{ width: 44, height: 44, alignItems: 'center', justifyContent: 'center', opacity: index === 0 ? 0.35 : 1 }}>
              <Icon name="chevronLeft" context="toolbar" />
            </Pressable>
            <Pressable testID="week-this" accessibilityRole="button" accessibilityLabel="This week" onPress={() => go(current)}
              style={{ minHeight: 44, paddingHorizontal: 8, justifyContent: 'center' }}>
              <Text variant="subhead" tone="muted" style={{ fontWeight: '600' }}>This week</Text>
            </Pressable>
            <Pressable testID="week-next" accessibilityRole="button" accessibilityLabel="Next week" disabled={index === vm.weeks.length - 1}
              onPress={() => go(index + 1)} style={{ width: 44, height: 44, alignItems: 'center', justifyContent: 'center', opacity: index === vm.weeks.length - 1 ? 0.35 : 1 }}>
              <Icon name="chevronRight" context="toolbar" />
            </Pressable>
          </View>
        </View>
        <Text variant="footnote" tone="muted" numeric testID="week-summary">{week.summary}</Text>
      </View>

      <FlatList
        ref={list}
        data={vm.weeks}
        horizontal
        pagingEnabled
        scrollEnabled={!dragging}
        showsHorizontalScrollIndicator={false}
        keyExtractor={(w) => w.start}
        initialScrollIndex={index}
        getItemLayout={(_, i) => ({ length: width, offset: width * i, index: i })}
        onViewableItemsChanged={onViewable}
        viewabilityConfig={{ itemVisiblePercentThreshold: 60 }}
        renderItem={({ item }) => <WeekPage week={item} today={vm.today} width={width} actions={actions} onDragging={setDrag} />}
        accessibilityLabel="Weeks"
      />

      <View style={{ paddingHorizontal: 16, gap: 8 }}>
        {pending?.kind === 'warn' ? (
          <View testID="drag-warning" accessibilityLiveRegion="polite" style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: 12, backgroundColor: colors.warningTint }}>
            <Icon name="warning" tone="warning" />
            <View style={{ flex: 1, gap: 8 }}>
              <Text variant="subhead"><Text variant="subhead" style={{ fontWeight: '700' }}>{pending.title}</Text> {pending.words}</Text>
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                <Button label="Move anyway" small full onPress={actions.moveAnyway} />
                <Button label={pending.keepLabel} kind="primary" brand={brand} small full onPress={actions.keep} />
              </View>
            </View>
          </View>
        ) : pending?.kind === 'refused' ? (
          <Pressable testID="drag-refused" accessibilityRole="button" accessibilityLiveRegion="polite" onPress={actions.keep}
            style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: 12, backgroundColor: colors.warningTint, alignItems: 'center' }}>
            <Icon name="warning" tone="warning" />
            <Text variant="subhead" style={{ flex: 1 }}>{pending.message}</Text>
            <Text variant="subhead" style={{ fontWeight: '600' }}>OK</Text>
          </Pressable>
        ) : proposalText ? (
          <View testID="proposal" accessibilityLiveRegion="polite" style={{ gap: 8, padding: 12, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
            <Text variant="subhead">{proposalText} Nothing changes on your watch until you confirm.</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Button label="Undo" small full onPress={actions.undo} />
              <Button label="Review and send" kind="primary" brand={brand} small full onPress={actions.review} />
            </View>
          </View>
        ) : (
          <Text variant="caption" tone="muted" style={{ textAlign: 'center' }}>Swipe for other weeks. Hold and drag a session to move it.</Text>
        )}
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Button label="Life happened" full onPress={actions.life} />
          {os === 'ios' ? <Button label="New workout" icon="add" full onPress={actions.newWorkout} /> : null}
        </View>
      </View>
    </View>
  );
}
