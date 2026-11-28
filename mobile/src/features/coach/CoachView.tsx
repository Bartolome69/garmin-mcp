// The Coach tab (build prompt, sections 2 and 5.10; boards P3-Coach-conv, -menu,
// -fromrun, P1-Coach-confirm, CoachEmpty/Error/Limit/Offline/Streaming): one
// conversation. Older days collapse; a question asked from Today, a run or a session
// carries its tag; replies are serif prose with source chips and, where the server
// sent one, a chart the app draws from the server's numbers; a proposed change is a
// card that opens the shared diff and the watch confirm. Prompt chips sit docked above
// the composer, never over the thread. The Coach has no hue: a muted mono label, serif
// prose and a neutral send button. The screen's one brand fill is the newest
// proposal's "Review" button, and it loses it behind a sheet.
//
// The keyboard (milestone 12): the screen never moves. The header stays, the thread
// (a virtualised list) shrinks and stays on the newest message, the composer sits on
// the keyboard, and the tab bar hides while it's up. Each message row is memoised, so a
// streamed token redraws only the reply it belongs to.
import { memo, useCallback, useMemo, useRef, useState, type ComponentRef } from 'react';
import { ActivityIndicator, FlatList, Pressable, ScrollView, TextInput, useWindowDimensions, View, type LayoutChangeEvent } from 'react-native';

import type { FailureKind } from '../../data/resource';
import type { GuardKind } from '../../domain/replyGuard';
import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { keyboardOverlap, useKeyboard } from '../../ui/keyboard';
import { useAnnounce } from '../../ui/motion';
import { FailureBanner } from '../../ui/StateViews';
import { WatchStatus } from '../../ui/session/WatchStatus';
import { TYPE_TOKEN } from '../../ui/session/types';
import { Text } from '../../ui/Text';
import type { WatchState } from '../today/model';
import type { DiffSession, Proposal } from '../watch/model';
import { MEDICAL } from '../watch/proposals';
import { InlineChartView } from './InlineChart';
import { inPlanFor, type CoachMessage, type ContextTag, type Limit, type ThreadRow } from './model';

/** Where a proposal card stands: still to review, or following its watch operations. */
export type CardState = {
  proposal: Proposal | null;          // staged on the phone, when it could be worked out
  canReview: boolean;                 // it can open the shared diff (the server said how)
  batch: { state: WatchState; at: string | null; error: string | null; opId: string | null } | null;
};

export type CoachActions = {
  send: (text: string, starter?: boolean) => void;
  sendAgain: (id: string) => void;
  retry: (replyId: string) => void;
  removeContext: () => void;
  feedback: (id: string, value: 'up' | 'down') => void;
  review: (m: CoachMessage) => void;
  decline: (id: string) => void;
  /** Finish a proposal in Plan (inPlanFor says where and what to do there). */
  inPlan: (m: CoachMessage) => void;
  resend: (opId: string) => void;
  showEarlier: () => void;
  limit: () => void;
  editMemory: (id: string) => void;
  retryLoad: () => void;
  endSearch: () => void;
  clearFilter: () => void;
};

export function flagWords(flags: GuardKind[]): string | null {
  if (flags.includes('medical') || flags.includes('diagnosis')) return MEDICAL;
  if (flags.includes('cause') || flags.includes('certainty')) return 'Read this as an observation. Daash can’t tell what caused it, or what will happen.';
  return null;
}

function Tag({ tag }: { tag: ContextTag }) {
  const { colors } = useTheme();
  return (
    <View testID="context-tag" accessible accessibilityLabel={tag.label}
      style={{ alignSelf: 'flex-end', flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 28, paddingHorizontal: 10, paddingVertical: 4,
        borderRadius: 999, backgroundColor: colors.sunken }}>
      <Icon name="link" tone="muted" />
      <Text variant="caption" tone="muted" numeric style={{ flexShrink: 1 }}>{tag.label}</Text>
    </View>
  );
}

function Sources({ list }: { list: string[] }) {
  const { colors } = useTheme();
  if (!list.length) return null;
  return (
    <View testID="sources" accessible accessibilityLabel={`Sources: ${list.join(', ')}`} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
      {list.map((s) => (
        <View key={s} style={{ borderWidth: 1, borderColor: colors.hairline, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 1 }}>
          <Text variant="caption" tone="muted">{s}</Text>
        </View>
      ))}
    </View>
  );
}

function Thumb({ up, on, onPress }: { up: boolean; on: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={up ? 'Helpful' : 'Not helpful'} accessibilityState={{ selected: on }}
      testID={up ? 'thumb-up' : 'thumb-down'} onPress={onPress} hitSlop={(MIN_TOUCH - 34) / 2}
      style={{ width: 34, height: 34, borderRadius: 999, borderWidth: 1, borderColor: on ? colors.text : colors.hairline,
        backgroundColor: on ? colors.sunken : 'transparent', alignItems: 'center', justifyContent: 'center' }}>
      <Icon name={up ? 'thumbUp' : 'thumbDown'} tone={on ? 'text' : 'muted'} />
    </Pressable>
  );
}

const Reply = memo(function Reply({ m, actions }: { m: CoachMessage; actions: CoachActions }) {
  const { colors } = useTheme();
  const streaming = m.status === 'streaming';
  const note = flagWords(m.flags ?? []);
  // Said once it's finished, not token by token (a live region on the text would read every word as it lands).
  useAnnounce(!streaming && m.text ? `The Coach: ${m.text}` : null);
  return (
    <View testID={`coach-reply-${m.id}`}
      style={{ alignSelf: 'flex-start', maxWidth: '92%', padding: 14, paddingVertical: 12, borderRadius: 18, borderBottomLeftRadius: 6,
        backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
      {m.text ? <Text variant="coach" testID={`reply-text-${m.id}`}>{m.text}</Text> : null}
      {streaming ? (
        <View testID="streaming" accessible accessibilityLabel={m.working ?? 'Writing'} accessibilityLiveRegion="polite" style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: m.text ? 6 : 0 }}>
          <ActivityIndicator size="small" color={colors.muted} />
          <Text variant="footnote" tone="muted">{m.working ?? 'Writing'}</Text>
        </View>
      ) : null}
      {m.chart ? <InlineChartView chart={m.chart} testID={`chart-${m.id}`} /> : null}
      {!streaming ? <Sources list={m.sources ?? []} /> : null}
      {note ? (
        <View testID={`flag-${m.id}`} accessible accessibilityLabel={note} style={{ flexDirection: 'row', gap: 6, marginTop: 8, alignItems: 'flex-start' }}>
          <Icon name="info" tone="muted" />
          <Text variant="footnote" tone="muted" style={{ flex: 1 }}>{note}</Text>
        </View>
      ) : null}
      {m.error ? (
        <View style={{ gap: 6, marginTop: 8 }}>
          <View accessible accessibilityLabel={m.error} style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
            <Icon name="error" tone="brandText" />
            <Text variant="footnote" tone="brandText" style={{ flex: 1 }} testID={`reply-error-${m.id}`}>{m.error}</Text>
          </View>
          <Button label="Try again" small onPress={() => actions.retry(m.id)} style={{ alignSelf: 'flex-start' }} />
        </View>
      ) : null}
      {(m.status === 'done' || m.status === 'partial') && !/^local:/.test(m.id) ? (
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 10 }}>
          <Thumb up on={m.feedback === 'up'} onPress={() => actions.feedback(m.id, 'up')} />
          <Thumb up={false} on={m.feedback === 'down'} onPress={() => actions.feedback(m.id, 'down')} />
        </View>
      ) : null}
    </View>
  );
});

const Question = memo(function Question({ m, actions, blocked }: { m: CoachMessage; actions: CoachActions; blocked: boolean }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: 6, alignSelf: 'stretch' }}>
      {m.context ? <Tag tag={m.context} /> : null}
      <View testID={`question-${m.id}`} style={{ alignSelf: 'flex-end', maxWidth: '85%', paddingHorizontal: 14, paddingVertical: 12,
        borderRadius: 18, borderBottomRightRadius: 6, backgroundColor: colors.button, opacity: m.status === 'sending' ? 0.7 : 1 }}>
        <Text variant="body">{m.text}</Text>
      </View>
      {m.status === 'not_sent' ? (
        <View style={{ alignSelf: 'flex-end', alignItems: 'flex-end', gap: 4 }}>
          <View accessible accessibilityLabel={`Not sent. ${m.error ?? ''}`} style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
            <Icon name="error" tone="brandText" />
            <Text variant="footnote" tone="brandText" testID={`not-sent-${m.id}`}>{`Not sent. ${m.error ?? ''}`.trim()}</Text>
          </View>
          {!blocked ? <Button label="Send again" small onPress={() => actions.sendAgain(m.id)} /> : null}
        </View>
      ) : null}
    </View>
  );
});

const sessionWords = (s: DiffSession | null) => (s ? s.title : 'Rest');
const sameCard = (a: CardState, b: CardState) => a.proposal === b.proposal && a.canReview === b.canReview
  && a.batch?.state === b.batch?.state && a.batch?.at === b.batch?.at && a.batch?.error === b.batch?.error && a.batch?.opId === b.batch?.opId;
const NO_CARD: CardState = { proposal: null, canReview: false, batch: null };

/** The same actions object across renders, each calling the latest handler: memoised rows don't redraw for a new closure. */
function useStableActions<T extends Record<string, (...args: never[]) => unknown>>(actions: T): T {
  const latest = useRef(actions);
  latest.current = actions;
  return useMemo(() => Object.fromEntries(Object.keys(actions).map((k) => [k, (...args: never[]) => latest.current[k](...args)])) as T,
    []); // eslint-disable-line react-hooks/exhaustive-deps
}

const rowKey = (r: ThreadRow, i: number) => (r.kind === 'message' ? r.m.id : r.kind === 'earlier' ? 'earlier' : `day-${r.label}-${i}`);

const Card = memo(function Card({ m, card, actions, brand }: { m: CoachMessage; card: CardState; actions: CoachActions; brand: boolean }) {
  const { colors } = useTheme();
  const p = card.proposal;
  const n = p?.ops.length ?? 0;
  const pending = m.status === 'pending' && !card.batch;
  const inPlan = inPlanFor(m.tool);
  return (
    <View testID={`proposal-${m.id}`} style={{ alignSelf: 'stretch', gap: 10, padding: 16, borderRadius: 16, backgroundColor: colors.surface,
      borderWidth: 1, borderColor: colors.hairline }}>
      <Text variant="kicker" accessibilityRole="header" tone="muted">Proposed change</Text>
      {p && p.days.some((d) => d.changed) ? (
        <View testID={`proposal-days-${m.id}`} style={{ gap: 4 }}>
          {p.days.filter((d) => d.changed).map((d) => (
            <View key={d.date} accessible accessibilityLabel={`${d.label}: ${sessionWords(d.before)}, becomes ${sessionWords(d.after)}`}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 32 }}>
              <Text variant="kicker" tone="muted" style={{ width: 52 }} maxFontSizeMultiplier={1.5}>{d.label}</Text>
              <Text variant="subhead" tone="muted" numberOfLines={2} style={{ flex: 1, textDecorationLine: 'line-through' }}>{sessionWords(d.before)}</Text>
              <View style={{ width: 4, height: 16, borderRadius: 2, backgroundColor: d.after ? colors[TYPE_TOKEN[d.after.type]] : 'transparent',
                borderWidth: d.after ? 0 : 1.5, borderStyle: 'dashed', borderColor: colors.cRest }} />
              <Text variant="subhead" numberOfLines={2} style={{ flex: 1, fontWeight: '600' }}>{sessionWords(d.after)}</Text>
            </View>
          ))}
        </View>
      ) : <Text variant="subhead" testID={`proposal-text-${m.id}`}>{m.text}</Text>}

      {card.batch ? (
        <View testID={`proposal-sent-${m.id}`} style={{ gap: 8 }}>
          <WatchStatus state={card.batch.state} at={card.batch.at} />
          {card.batch.state === 'failed' ? (
            <>
              {card.batch.error ? <Text variant="footnote" tone="brandText">{card.batch.error}</Text> : null}
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                <Button label="Resend" kind="primary" brand={brand} small onPress={() => card.batch?.opId && actions.resend(card.batch.opId)} />
                <Button label="Do it in Plan" small onPress={() => actions.inPlan(m)} />
              </View>
              <Text variant="footnote" tone="muted">Nothing changed on your watch.</Text>
            </>
          ) : null}
        </View>
      ) : pending ? (
        <>
          {!card.canReview ? <Text variant="footnote" tone="muted" testID={`proposal-hint-${m.id}`}>{inPlan.hint}</Text> : null}
          <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
            {card.canReview
              ? <Button label={n ? `Review ${n} watch change${n === 1 ? '' : 's'}` : 'Review the change'} kind="primary" brand={brand} small onPress={() => actions.review(m)} />
              : <Button label={inPlan.label} small onPress={() => actions.inPlan(m)} />}
            <Button label="Not now" small onPress={() => actions.decline(m.id)} />
          </View>
        </>
      ) : (
        <Text variant="footnote" tone="muted" testID={`proposal-status-${m.id}`}>
          {m.status === 'declined' ? 'Not now · nothing changed' : m.status === 'expired' ? 'This proposal expired. Ask again for a new one.'
            : m.status === 'done' ? 'Done' : m.status === 'failed' ? 'Not made. Nothing changed.' : 'Being made now'}
        </Text>
      )}
    </View>
  );
}, (a, b) => a.m === b.m && a.brand === b.brand && a.actions === b.actions && sameCard(a.card, b.card));

const MemoryNote = memo(function MemoryNote({ m, actions }: { m: CoachMessage; actions: CoachActions }) {
  const onEdit = () => m.memoryId && actions.editMemory(m.memoryId);
  return (
    <View testID={`memory-note-${m.id}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap', alignSelf: 'flex-start' }}>
      <Icon name="check" tone="muted" />
      <Text variant="footnote" tone="muted" style={{ flexShrink: 1 }}>{`Saved to memory: ${m.text}`}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel={`Edit memory: ${m.text}`} onPress={onEdit}
        style={{ minHeight: MIN_TOUCH, minWidth: MIN_TOUCH, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4 }}>
        <Text variant="footnote" tone="brandText" style={{ fontWeight: '600' }}>Edit</Text>
      </Pressable>
    </View>
  );
});

function Chip({ label, onPress, disabled }: { label: string; onPress: () => void; disabled: boolean }) {
  const { colors } = useTheme();
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} testID={`chip-${label}`}
      style={{ minHeight: MIN_TOUCH, justifyContent: 'center', opacity: disabled ? 0.4 : 1 }}>
      <View style={{ minHeight: 34, paddingHorizontal: 14, justifyContent: 'center', borderRadius: 999, borderWidth: 1, borderColor: colors.muted }}>
        <Text variant="subhead" style={{ fontWeight: '500' }}>{label}</Text>
      </View>
    </Pressable>
  );
}

function Banner({ testID, children, onPress, label }: { testID: string; children: React.ReactNode; onPress?: () => void; label: string }) {
  const { colors } = useTheme();
  const body = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: 12, backgroundColor: colors.warningTint, minHeight: MIN_TOUCH }}>
      <Icon name="warning" tone="warning" />
      <View style={{ flex: 1 }}>{children}</View>
    </View>
  );
  return onPress
    ? <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label} onPress={onPress}>{body}</Pressable>
    : <View testID={testID} accessibilityLiveRegion="polite">{body}</View>;
}

export function CoachView({ rows, empty, starters, limit, offline, streaming, context, searching, query, onQuery, filter, cards, latestPending,
  brand, actions: given, loadFailed, authFailure = null }: {
  rows: ThreadRow[];
  /** No conversation yet (not a search with no results). */
  empty: boolean;
  starters: string[];
  limit: Limit;
  offline: boolean;
  streaming: boolean;
  context: ContextTag | null;
  searching: boolean;
  query: string;
  onQuery: (q: string) => void;
  filter: ContextTag | null;
  cards: Record<string, CardState>;
  /** The newest proposal still to review: the screen's one brand fill. */
  latestPending: string | null;
  brand: boolean;
  actions: CoachActions;
  /** The thread couldn't load and nothing is cached. */
  loadFailed: string | null;
  /** Signed out, or Garmin needs a new sign-in: said over the thread, cached or not, with its own button; nothing can be sent. */
  authFailure?: Extract<FailureKind, 'signedOut' | 'reconnect'> | null;
}) {
  const { colors } = useTheme();
  const [text, setText] = useState('');
  const list = useRef<ComponentRef<typeof FlatList<ThreadRow>>>(null);
  const actions = useStableActions(given);
  const locked = limit.reached;
  const blocked = offline || locked || !!authFailure;
  const canSend = !!text.trim() && !streaming && !blocked;

  // The keyboard: how far it covers this screen, from the screen's bottom edge in the window
  // (measured; until it is, the window's own bottom). The dock is padded by that much.
  const keyboard = useKeyboard();
  const { height: windowHeight } = useWindowDimensions();
  const root = useRef<ComponentRef<typeof View>>(null);
  const [bottom, setBottom] = useState<number | null>(null);
  const measure = useCallback((_e?: LayoutChangeEvent) => {
    root.current?.measureInWindow?.((_x: number, y: number, _w: number, h: number) => { if (typeof h === 'number' && h > 0) setBottom(y + h); });
  }, []);
  const inset = keyboardOverlap(bottom ?? windowHeight, keyboard);

  // Stay on the newest message while the runner is there: as replies stream in, and when the
  // keyboard shrinks the list. Scrolling up to read stops it until they're back at the end.
  const pinned = useRef(true);
  const toEnd = useCallback(() => { if (pinned.current && !searching && !filter) list.current?.scrollToEnd?.({ animated: false }); }, [searching, filter]);

  const submit = (words: string, starter = false) => {
    if (!words.trim() || streaming || blocked) return;
    pinned.current = true;
    actions.send(words, starter);
    if (!starter) setText('');
  };

  const renderRow = ({ item: r }: { item: ThreadRow }) => {
    if (r.kind === 'earlier') {
      return (
        <Pressable testID="coach-earlier" accessibilityRole="button" accessibilityLabel={`${r.label}. Show`} onPress={actions.showEarlier}
          style={{ minHeight: MIN_TOUCH, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 12,
            borderRadius: 999, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.hairline }}>
          <Text variant="footnote" tone="muted">{r.label}</Text>
          <Text variant="footnote" style={{ fontWeight: '600' }}>Show</Text>
        </Pressable>
      );
    }
    if (r.kind === 'day') return <Text testID="coach-day" variant="kicker" tone="muted" accessibilityRole="header" style={{ alignSelf: 'center', paddingVertical: 4 }} numeric>{r.label}</Text>;
    const m = r.m;
    if (m.role === 'user') return <Question m={m} actions={actions} blocked={blocked} />;
    if (m.role === 'assistant') return <Reply m={m} actions={actions} />;
    if (m.role === 'memory') return <MemoryNote m={m} actions={actions} />;
    return <Card m={m} card={cards[m.id] ?? NO_CARD} actions={actions} brand={brand && latestPending === m.id} />;
  };

  const top = loadFailed && empty ? (
    <View testID="coach-error" style={{ gap: 10 }}>
      <Text variant="headline" accessibilityRole="header">Couldn’t open the conversation</Text>
      <Text variant="body" tone="muted">{loadFailed}</Text>
      <Button label="Try again" onPress={actions.retryLoad} style={{ alignSelf: 'flex-start' }} />
    </View>
  ) : empty ? (
    <View testID="coach-empty" style={{ gap: 8, paddingTop: 8 }}>
      <Text variant="kicker" tone="muted">Coach</Text>
      <Text variant="coach">Ask about your plan, your runs or how today looks. The Coach reads the same numbers as the rest of Daash and proposes changes you can review.</Text>
      <Text variant="footnote" tone="muted">{MEDICAL}</Text>
    </View>
  ) : (searching || filter) && !rows.length ? (
    <Text variant="body" tone="muted" testID="coach-no-results">{searching ? (query.trim() ? 'Nothing in the conversation matches.' : 'Type to search the conversation.') : 'Nothing here yet.'}</Text>
  ) : null;

  return (
    <View ref={root} testID="coach" onLayout={measure} style={{ flex: 1, backgroundColor: colors.bg }}>
      {offline ? (
        <View style={{ paddingHorizontal: 16, paddingTop: 8 }}>
          <Banner testID="coach-offline" label="Offline">
            <Text variant="subhead">Offline · the Coach needs a connection. Your plan and runs still work.</Text>
            <Button label="Try again" small onPress={actions.retryLoad} style={{ alignSelf: 'flex-start', marginTop: 6 }} />
          </Banner>
        </View>
      ) : null}
      <FailureBanner failure={authFailure} />
      {searching ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, paddingTop: 8 }}>
          <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, borderRadius: 12, backgroundColor: colors.sunken, minHeight: MIN_TOUCH }}>
            <Icon name="search" tone="muted" />
            <TextInput testID="coach-search-input" value={query} onChangeText={onQuery} placeholder="Search the conversation" placeholderTextColor={colors.muted}
              autoFocus accessibilityLabel="Search the conversation" style={{ flex: 1, color: colors.text, fontSize: 16, minHeight: MIN_TOUCH }} />
          </View>
          <Button label="Done" small onPress={actions.endSearch} />
        </View>
      ) : null}
      {filter ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, paddingTop: 8 }}>
          <Text variant="subhead" tone="muted" style={{ flex: 1 }} testID="coach-filter">{filter.label}</Text>
          <Button label="Show all" small onPress={actions.clearFilter} />
        </View>
      ) : null}

      <FlatList ref={list} testID="coach-thread" data={rows} keyExtractor={rowKey} renderItem={renderRow}
        ListHeaderComponent={top ?? undefined} style={{ flex: 1 }}
        contentContainerStyle={{ padding: 16, gap: 10, width: '100%', maxWidth: 720, alignSelf: 'center' }}
        keyboardDismissMode="interactive" keyboardShouldPersistTaps="handled" contentInsetAdjustmentBehavior="automatic"
        initialNumToRender={20} windowSize={11} removeClippedSubviews={false}
        onContentSizeChange={toEnd} onLayout={toEnd}
        onScroll={(e) => {
          const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
          pinned.current = contentOffset.y + layoutMeasurement.height >= contentSize.height - 48;
        }}
        scrollEventThrottle={64} />

      <View testID="coach-dock" style={{ backgroundColor: colors.bg, borderTopWidth: 1, borderTopColor: colors.hairline, paddingTop: 8,
        paddingBottom: inset ? inset + 8 : 10, gap: 6 }}>
        {locked ? (
          <View style={{ paddingHorizontal: 16 }}>
            <Banner testID="coach-limit" label={`You’ve used today’s ${limit.limit} free messages. See what Plus changes`} onPress={actions.limit}>
              <Text variant="subhead">{`You’ve used today’s ${limit.limit} free messages`}</Text>
              <Text variant="footnote" tone="muted">A change already proposed can still be reviewed.</Text>
            </Banner>
          </View>
        ) : starters.length && !searching ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} testID="coach-chips" contentContainerStyle={{ paddingHorizontal: 16, gap: 8 }}>
            {starters.map((s) => <Chip key={s} label={s} disabled={streaming || offline || !!authFailure} onPress={() => submit(s, true)} />)}
          </ScrollView>
        ) : null}
        {context ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 16 }}>
            <View style={{ flexShrink: 1 }}><Tag tag={context} /></View>
            <Pressable accessibilityRole="button" accessibilityLabel={`Remove ${context.label}`} testID="context-remove" onPress={actions.removeContext}
              style={{ width: MIN_TOUCH, height: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
              <Icon name="close" tone="muted" />
            </Pressable>
          </View>
        ) : null}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 16, paddingLeft: 16, paddingRight: 4, borderRadius: 24,
          backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, minHeight: MIN_TOUCH + 8 }}>
          <TextInput testID="composer" value={text} onChangeText={setText} multiline editable={!locked}
            placeholder={offline ? 'The Coach needs a connection' : 'Ask the Coach'} placeholderTextColor={colors.muted}
            accessibilityLabel="Message the Coach" style={{ flex: 1, color: colors.text, fontSize: 16, paddingVertical: 10, maxHeight: 120 }} />
          {limit.remaining != null && !locked ? (
            <Text variant="caption" tone="muted" numeric testID="coach-remaining">{`${limit.remaining} left today`}</Text>
          ) : null}
          <Pressable testID="coach-send" accessibilityRole="button" accessibilityLabel="Send" accessibilityState={{ disabled: !canSend }}
            disabled={!canSend} onPress={() => submit(text)}
            style={{ width: MIN_TOUCH, height: MIN_TOUCH, alignItems: 'center', justifyContent: 'center', opacity: canSend ? 1 : 0.4 }}>
            <Icon name="send" context="toolbar" tone={canSend ? 'text' : 'muted'} />
          </Pressable>
        </View>
      </View>
    </View>
  );
}
