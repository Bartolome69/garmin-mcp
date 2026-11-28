import { Redirect, Stack, useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef } from 'react';
import { Pressable, View } from 'react-native';

import { useCoach } from '../../../features/coach/CoachProvider';
import { CoachView, type CardState } from '../../../features/coach/CoachView';
import { contextFrom, filterByTag, hhmm, searchThread, tagKey, threadRows, type CoachMessage } from '../../../features/coach/model';
import type { WatchOp } from '../../../features/watch/queue';
import { useWatch } from '../../../features/watch/WatchProvider';
import { useFlags } from '../../../flags/FlagsProvider';
import { MIN_TOUCH } from '../../../ui/Button';
import { Icon } from '../../../ui/Icon';

/** One state for a proposal's watch operations: a failure first, then sending, then on the watch once every one is. */
function batchState(ops: WatchOp[]): CardState['batch'] {
  if (!ops.length) return null;
  const failed = ops.find((o) => o.state === 'failed');
  if (failed) return { state: 'failed', at: null, error: failed.error, opId: failed.id };
  if (ops.some((o) => o.state === 'sending')) return { state: 'sending', at: null, error: null, opId: null };
  if (ops.every((o) => o.state === 'on_watch')) {
    const at = Math.max(...ops.map((o) => o.onWatchAt ?? 0));
    return { state: 'on_watch', at: at ? hhmm(at) : null, error: null, opId: null };
  }
  return { state: 'changed_pending_sync', at: null, error: null, opId: null };
}

// The Coach (build prompt, section 5.10). Only reachable with coach_enabled; the tab is hidden otherwise.
export default function CoachScreen() {
  const { flags } = useFlags();
  const coach = useCoach();
  const watch = useWatch();
  const router = useRouter();
  const focused = useIsFocused();
  const params = useLocalSearchParams<{ about?: string; run?: string; session?: string; label?: string }>();
  const resolving = useRef(new Set<string>());

  // Opened from Today, a run or a session: the next question carries that tag.
  const paramKey = [params.about, params.run, params.session, params.label].join('|');
  const lastKey = useRef('');
  useEffect(() => {
    if (paramKey === lastKey.current) return;
    lastKey.current = paramKey;
    const tag = contextFrom(params);
    if (tag) coach.setContext(tag);
  }, [paramKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // A proposal the server described is staged on the phone, so its card shows the changed days.
  useEffect(() => {
    for (const m of coach.messages) {
      if (m.role !== 'action' || m.status !== 'pending' || m.proposalId || resolving.current.has(m.id)) continue;
      if (m.proposal) { watch.stage(m.proposal); coach.linkProposal(m.id, m.proposal.id); continue; }
      if (!m.request) continue;
      resolving.current.add(m.id);
      watch.propose(m.request).then((p) => coach.linkProposal(m.id, p.id)).catch(() => {}).finally(() => resolving.current.delete(m.id));
    }
  }, [coach.messages]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!flags.coach_enabled) return <Redirect href="/today" />;

  const cards: Record<string, CardState> = {};
  let latestPending: string | null = null;
  for (const m of coach.messages) {
    if (m.role !== 'action') continue;
    const batch = m.proposalId ? batchState(watch.ops.filter((o) => o.batch.startsWith(`${m.proposalId}@`))) : null;
    const canReview = !!(m.proposal || m.request);
    cards[m.id] = { proposal: (m.proposalId ? watch.draft(m.proposalId) : null) ?? m.proposal ?? null, canReview, batch };
    if (m.status === 'pending' && !batch && canReview) latestPending = m.id;
  }

  let shown: CoachMessage[] = coach.messages;
  if (coach.filter) shown = filterByTag(shown, tagKey(coach.filter));
  const searching = coach.searching;
  if (searching) shown = searchThread(shown, coach.query);
  const now = Date.now();
  const rows = threadRows(shown, now, coach.showEarlier || searching || !!coach.filter);

  const review = async (m: CoachMessage) => {
    let p = m.proposalId ? watch.draft(m.proposalId) : null;
    try {
      if (!p && m.proposal) { watch.stage(m.proposal); p = m.proposal; }
      if (!p && m.request) p = await watch.propose(m.request);
    } catch { p = null; }
    if (!p) { router.push('/plan'); return; }
    coach.linkProposal(m.id, p.id);
    router.push({ pathname: '/coach/new-week', params: { id: p.id } });
  };

  const iconButton = (name: 'search' | 'more', label: string, onPress: () => void) => (
    <Pressable accessibilityRole="button" accessibilityLabel={label} testID={`coach-${name}`} onPress={onPress}
      style={{ width: MIN_TOUCH, height: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
      <Icon name={name} context="toolbar" />
    </Pressable>
  );

  return (
    <>
      <Stack.Screen options={{ headerRight: () => (
        <View style={{ flexDirection: 'row', gap: 2 }}>
          {iconButton('search', 'Search the conversation', () => coach.setSearching(true))}
          {iconButton('more', 'Conversation options', () => router.push('/coach/menu'))}
        </View>
      ) }} />
      <CoachView
        rows={rows}
        empty={!coach.messages.length}
        starters={coach.starters}
        limit={coach.limit}
        offline={coach.failure === 'offline'}
        streaming={coach.streaming}
        context={coach.context}
        searching={searching}
        query={coach.query}
        onQuery={coach.setQuery}
        filter={coach.filter}
        cards={cards}
        latestPending={latestPending}
        brand={focused}
        loadFailed={coach.loaded && coach.failure && coach.failure !== 'offline' ? coach.error ?? 'Daash didn’t answer. Try again in a moment.' : null}
        actions={{
          send: (text, starter) => { coach.send(text, { starter }); },
          sendAgain: (id) => { coach.sendAgain(id); },
          retry: (replyId) => {
            const i = coach.messages.findIndex((m) => m.id === replyId);
            const q = [...coach.messages.slice(0, i)].reverse().find((m) => m.role === 'user');
            if (q) coach.send(q.text, { context: q.context ?? null });
          },
          removeContext: () => coach.setContext(null),
          feedback: (id, v) => { coach.feedback(id, v); },
          review: (m) => { review(m); },
          decline: (id) => { coach.decline(id); },
          inPlan: () => router.push('/plan'),
          resend: (opId) => watch.resend(opId),
          showEarlier: () => coach.setShowEarlier(true),
          limit: () => router.push('/coach/limit'),
          editMemory: (id) => router.push({ pathname: '/you/memory-edit', params: { id } }),
          retryLoad: () => { coach.refresh(); },
          endSearch: () => { coach.setSearching(false); coach.setQuery(''); },
          clearFilter: () => coach.setFilter(null),
        }}
      />
    </>
  );
}
