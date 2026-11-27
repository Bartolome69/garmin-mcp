// One day of the week (build prompt, section 5.3): date, type rail, title, type
// label and key numbers, and a status icon with distinct shapes (a filled tick for
// done, an outline watch for on your watch), each with words for screen readers.
// No chevron: the row opens its session. Hold and drag moves it (a long press
// lifts the row, then React Native's PanResponder takes the moves that follow);
// VoiceOver and TalkBack get the same move as "a day earlier" / "a day later".
import { useMemo, useRef } from 'react';
import { ActivityIndicator, Animated, PanResponder, Pressable, View, type LayoutChangeEvent } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Icon } from '../../ui/Icon';
import { TYPE_TOKEN } from '../../ui/session/types';
import { watchWords } from '../../ui/session/WatchStatus';
import { Text } from '../../ui/Text';
import type { PlanDay, PlanSession } from './model';

/** How long a press must last before the row lifts. */
export const LIFT_AFTER_MS = 350;

export function statusWords(s: PlanSession): string | null {
  if (s.done) return 'Done';
  if (!s.watch || s.watch.state === 'not_sent') return null;
  return s.watch.state === 'on_watch' ? 'On your watch' : watchWords(s.watch.state, s.watch.at);
}

function Status({ s }: { s: PlanSession }) {
  const { colors } = useTheme();
  const words = statusWords(s);
  if (!words) return <View style={{ width: 24 }} />;
  const state = s.done ? 'done' : s.watch!.state;
  return (
    <View testID={`status-${s.id}`} accessible accessibilityRole="image" accessibilityLabel={words}
      style={{ width: 24, height: 24, alignItems: 'center', justifyContent: 'center' }}>
      {state === 'sending' ? <ActivityIndicator size="small" color={colors.muted} />
        : <Icon
            name={state === 'done' ? 'done' : state === 'on_watch' ? 'onWatch' : state === 'failed' ? 'error' : 'sync'}
            tone={state === 'done' ? 'success' : state === 'on_watch' ? 'text2' : state === 'failed' ? 'brandText'
              : state === 'changed_pending_sync' ? 'warning' : 'muted'} />}
    </View>
  );
}

export type DragHandlers = {
  onStart: (id: string) => void;
  onMove: (dy: number) => void;
  onEnd: () => void;
};

export function DayRow({ day, session, first, onOpen, onAction, drag, lifted = false, dropping, target = false, translate, onLayout }: {
  day: PlanDay;
  session: PlanSession | null;
  first: boolean;
  onOpen?: (s: PlanSession) => void;
  /** The accessibility moves: -1 a day earlier, +1 a day later. */
  onAction?: (s: PlanSession, by: -1 | 1) => void;
  drag?: DragHandlers;
  lifted?: boolean;
  /** "dropping on Thu", while this row is being dragged. */
  dropping?: string | null;
  target?: boolean;
  translate?: Animated.Value;
  onLayout?: (e: LayoutChangeEvent) => void;
}) {
  const { colors } = useTheme();
  const started = useRef(false);   // lifted by the long press
  const panning = useRef(false);   // the pan responder has taken the touch
  const today = day.today;
  const s = session;
  const rest = s?.type === 'rest';
  const status = s ? statusWords(s) : null;
  const label = s
    ? [`${day.dow.charAt(0)}${day.dow.slice(1).toLowerCase()} ${day.day}${today ? ', today' : ''}`, s.title, s.sub.replace(/\/km/g, 'per km'),
      status ?? (s.watch ? 'Not on your watch' : null)].filter(Boolean).join(', ')
    : `${day.dow} ${day.day}, nothing planned`;

  // The latest handlers, for the responder made once below.
  const latest = useRef({ drag, id: s?.id });
  latest.current = { drag, id: s?.id };
  const end = () => { panning.current = false; if (started.current) { started.current = false; latest.current.drag?.onEnd(); } };
  const responder = useMemo(() => PanResponder.create({
    // Only once the long press has lifted the row: before that, a press is a tap and a swipe scrolls.
    onMoveShouldSetPanResponderCapture: () => started.current,
    onPanResponderGrant: () => { panning.current = true; },
    onPanResponderMove: (_, g) => latest.current.drag?.onMove(g.dy),
    onPanResponderRelease: end,
    onPanResponderTerminate: end,
    onPanResponderTerminationRequest: () => false,
  }), []); // eslint-disable-line react-hooks/exhaustive-deps

  const body = (
    <Animated.View
      onLayout={onLayout}
      testID={s ? `row-${s.id}` : `row-empty-${day.date}`}
      style={[{
        flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 56, paddingVertical: 6,
        borderTopWidth: first || today ? 0 : 1, borderTopColor: colors.hairline,
      }, today && { backgroundColor: colors.sunken, borderRadius: 12, marginHorizontal: -8, paddingHorizontal: 8 },
      target && !lifted && { backgroundColor: colors.sunken, borderRadius: 12 },
      lifted && {
        backgroundColor: colors.surface, borderWidth: 1.5, borderColor: colors.text, borderRadius: 12,
        marginHorizontal: -8, paddingHorizontal: 8, zIndex: 10, elevation: 8,
        transform: [{ translateY: translate ?? 0 }, { rotate: '-1deg' }],
      }]}
    >
      <View style={{ width: 38, alignItems: 'center' }}>
        <Text variant="kicker" tone={today ? 'brandText' : 'muted'} maxFontSizeMultiplier={1.5}>{day.dow}</Text>
        <Text variant="headline" tone={today ? 'brandText' : 'text'} numeric maxFontSizeMultiplier={1.5}>{day.day}</Text>
      </View>
      <View style={{ width: 4, height: 36, borderRadius: 2,
        backgroundColor: !s || rest ? 'transparent' : colors[TYPE_TOKEN[s.type]],
        borderWidth: !s || rest ? 1.5 : 0, borderStyle: 'dashed', borderColor: colors.cRest }} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text variant="subhead" style={{ fontWeight: '600' }} numberOfLines={2}>{s ? s.title : 'Nothing planned'}</Text>
        {s ? <Text variant="footnote" tone="muted" numeric numberOfLines={2}>{dropping ? `${s.typeLabel} · ${dropping}` : s.sub}</Text> : null}
      </View>
      {s ? <Status s={s} /> : <View style={{ width: 24 }} />}
    </Animated.View>
  );

  if (!s) return <View accessible accessibilityLabel={label}>{body}</View>;
  const opens = !rest && (s.done || !!s.detail);
  const movable = !!onAction && !!drag;
  return (
    <View {...(drag ? responder.panHandlers : {})}>
      <Pressable
        accessibilityRole={opens ? 'button' : undefined}
        accessibilityLabel={label}
        accessibilityHint={movable ? 'Hold and drag to move, or use the actions' : undefined}
        accessibilityActions={movable ? [{ name: 'earlier', label: 'Move a day earlier' }, { name: 'later', label: 'Move a day later' }] : undefined}
        onAccessibilityAction={(e) => { if (e.nativeEvent.actionName === 'earlier') onAction?.(s, -1); if (e.nativeEvent.actionName === 'later') onAction?.(s, 1); }}
        onPress={opens ? () => onOpen?.(s) : undefined}
        onLongPress={drag ? () => { started.current = true; drag.onStart(s.id); } : undefined}
        // A long press let go without moving puts the row back down. When the pan
        // takes the touch, the press ends too; then the pan's release ends the drag.
        onPressOut={() => { if (started.current) setTimeout(() => { if (started.current && !panning.current) end(); }, 0); }}
        delayLongPress={LIFT_AFTER_MS}
        testID={`open-${s.id}`}
      >
        {body}
      </Pressable>
    </View>
  );
}
