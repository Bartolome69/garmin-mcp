// Daash Plus, value first (build prompt, section 5.14; boards P3-Paywall-off/on,
// P1-PaySheet, YouPlus): a faded preview of the prediction trend, what Plus adds,
// what stays free, and "Try Plus free for 7 days". The trial sheet shows how the
// free week works, with the price from the store. Opened only by the runner's own
// tap on a lock or on You's Plus row; never in the middle of a flow.
import { ActivityIndicator, Platform, Pressable, ScrollView, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import { TrendPreview } from '../plan/TrendsView';
import { storeName, trialTimeline, type PlusStatus } from './entitlement';
import type { Product } from './store';

export type PlusItem = { title: string; sub: string };

/** What Plus adds (section 1). The Coach's line only with coach_enabled; its limit from the flags. */
export function plusItems(coach: boolean, coachLimit: number): PlusItem[] {
  return [
    { title: 'Race prediction trend', sub: 'and race-day projection' },
    { title: 'Advanced plan styles', sub: 'More shapes for your training block' },
    { title: 'Advanced merge rules', sub: 'Pick the source per metric, tune match windows' },
    { title: 'Home and lock screen widgets', sub: 'Readiness, week and countdown' },
    ...(coach ? [{ title: 'Coach without a daily limit', sub: `Free has ${coachLimit} messages a day` }] : []),
  ];
}

/** What stays free either way. */
export const ALWAYS_FREE = [
  'Plans, Today and readiness',
  'Sending workouts to your watch',
  'Connecting all your watches and apps',
  'Post-run analysis and the workout builder',
];

function Item({ title, sub, icon }: { title: string; sub?: string; icon: 'lock' | 'check' }) {
  return (
    <View accessible accessibilityLabel={sub ? `${title}, ${sub}` : title} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', paddingVertical: 6 }}>
      <Icon name={icon} tone={icon === 'check' ? 'success' : 'text'} />
      <View style={{ flex: 1, gap: 2 }}>
        <Text variant="body" style={{ fontWeight: '600' }}>{title}</Text>
        {sub ? <Text variant="footnote" tone="muted">{sub}</Text> : null}
      </View>
    </View>
  );
}

export function PaywallView({ coach, coachLimit, plus, trialOffered, brand, message, os = Platform.OS, canManage = true, actions }: {
  coach: boolean;
  coachLimit: number;
  /** Already on Plus or a trial: no offer, just where to manage it. */
  plus: boolean;
  /** Whether the store still offers this person the free week (null: not known yet, assume so). */
  trialOffered: boolean | null;
  brand: boolean;
  /** The outcome of Restore, in words. */
  message?: string | null;
  os?: string;
  /** On Plus with somewhere to manage it (a store or daash.run); a grant from Daash has nowhere. */
  canManage?: boolean;
  actions: { start: () => void; close: () => void; restore: () => void; manage: () => void };
}) {
  const { colors } = useTheme();
  return (
    <View testID="paywall" style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView contentInsetAdjustmentBehavior="automatic" contentContainerStyle={{ padding: 20, paddingTop: 12, gap: 14 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
          <Pressable testID="paywall-close" accessibilityRole="button" accessibilityLabel="Close" onPress={actions.close}
            style={{ minWidth: MIN_TOUCH, minHeight: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="close" tone="muted" context="toolbar" />
          </Pressable>
        </View>
        <Text variant="kicker" tone="muted">Daash Plus</Text>
        <Text variant="largeTitle" accessibilityRole="header">See where your training is heading.</Text>
        {/* Faded, with no figures: the person's own trend stays on the server until they have Plus. */}
        <View testID="paywall-preview" accessible accessibilityRole="image" style={{ opacity: 0.35 }}
          accessibilityLabel="A preview of the prediction trend and race-day projection">
          <TrendPreview />
        </View>
        <View testID="plus-items" style={{ gap: 2 }}>
          {plusItems(coach, coachLimit).map((i) => <Item key={i.title} icon="lock" title={i.title} sub={i.sub} />)}
        </View>
        <Text variant="kicker" tone="muted" style={{ marginTop: 6 }}>Always free</Text>
        <View testID="always-free" style={{ gap: 0 }}>
          {ALWAYS_FREE.map((t) => <Item key={t} icon="check" title={t} />)}
        </View>
        {message ? <Text testID="paywall-message" variant="footnote" tone="text2">{message}</Text> : null}
      </ScrollView>
      <View style={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 28, gap: 6, borderTopWidth: 1, borderTopColor: colors.hairline }}>
        {plus ? (
          <>
            <Text testID="paywall-have" variant="subhead" style={{ textAlign: 'center' }}>You have Daash Plus.</Text>
            {canManage ? <Button label="Manage subscription" onPress={actions.manage} /> : null}
          </>
        ) : (
          <Button label={trialOffered === false ? 'Get Plus' : 'Try Plus free for 7 days'} kind="primary" brand={brand} onPress={actions.start} />
        )}
        <View style={{ flexDirection: 'row', justifyContent: 'center', flexWrap: 'wrap', columnGap: 16 }}>
          <Pressable testID="paywall-restore" accessibilityRole="button" onPress={actions.restore} style={{ minHeight: MIN_TOUCH, justifyContent: 'center' }}>
            <Text variant="footnote" style={{ textDecorationLine: 'underline' }}>Restore purchases</Text>
          </Pressable>
        </View>
        <Text variant="caption" tone="muted" style={{ textAlign: 'center' }}>
          {`Plus renews until you cancel. Cancel anytime in ${storeName(os)}.`}
        </Text>
      </View>
    </View>
  );
}

/** "How your free week works" (P1-PaySheet): the timeline, the store's price, start or not now. */
export function TrialSheet({ product, loading, error, busy, outcome, brand, actions }: {
  /** The store's product; its price is the only price shown. */
  product: Product | null;
  loading: boolean;
  error: string | null;
  busy: boolean;
  /** What happened after Start, in words ("Waiting for approval…"). */
  outcome: string | null;
  brand: boolean;
  actions: { start: () => void; notNow: () => void; retry: () => void };
}) {
  const trial = product ? product.trialDays != null : true;
  const steps = trialTimeline(product?.price ?? null, product?.period ?? 'month');
  return (
    <ScrollView testID="trial-sheet" contentContainerStyle={{ padding: 20, paddingBottom: 32, gap: 14 }}>
      <Text variant="title" accessibilityRole="header">{trial ? 'How your free week works' : 'Daash Plus'}</Text>
      {trial ? (
        <View testID="trial-timeline" style={{ gap: 0 }}>
          {steps.map((s, i) => <Step key={s.when} when={s.when} what={s.what} first={i === 0} last={i === steps.length - 1} />)}
        </View>
      ) : product ? (
        <Text testID="trial-price" variant="body">{`${product.price} a ${product.period}, from today.`}</Text>
      ) : null}
      {loading ? (
        <View testID="trial-loading" accessible accessibilityLabel="Asking the store for the price" style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
          <ActivityIndicator size="small" />
          <Text variant="footnote" tone="muted">Asking the store for the price</Text>
        </View>
      ) : null}
      {error ? (
        <View testID="trial-error" style={{ gap: 8 }}>
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <Icon name="error" tone="muted" />
            <Text variant="footnote" style={{ flex: 1 }}>{error}</Text>
          </View>
          <View style={{ alignSelf: 'flex-start' }}><Button label="Try again" small onPress={actions.retry} /></View>
        </View>
      ) : null}
      <Text variant="subhead" style={{ fontWeight: '600' }}>{trial ? 'No payment due today. Cancel anytime.' : 'Cancel anytime.'}</Text>
      {outcome ? <Text testID="trial-outcome" variant="footnote" tone="text2">{outcome}</Text> : null}
      <View style={{ gap: 8 }}>
        <Button label={busy ? 'Starting…' : trial ? 'Start free week' : 'Get Plus'} kind="primary" brand={brand}
          disabled={!product || busy} onPress={actions.start} />
        <Button label="Not now" onPress={actions.notNow} />
      </View>
      <Text variant="footnote" tone="muted" style={{ textAlign: 'center' }}>Plans, Today and sending to your watch stay free either way.</Text>
    </ScrollView>
  );
}

function Step({ when, what, first, last }: { when: string; what: string; first: boolean; last: boolean }) {
  const { colors } = useTheme();
  return (
    <View accessible accessibilityLabel={`${when}: ${what}`} style={{ flexDirection: 'row', gap: 12 }}>
      <View style={{ width: 14, alignItems: 'center' }}>
        <View style={{ width: 2, flex: 1, backgroundColor: first ? 'transparent' : colors.hairline }} />
        <View style={{ width: 12, height: 12, borderRadius: 6, backgroundColor: first ? colors.text : colors.surface, borderWidth: 2, borderColor: colors.text }} />
        <View style={{ width: 2, flex: 1, backgroundColor: last ? 'transparent' : colors.hairline }} />
      </View>
      <View style={{ flex: 1, paddingVertical: 8, gap: 2 }}>
        <Text variant="subhead" style={{ fontWeight: '700' }}>{when}</Text>
        <Text variant="footnote" tone="muted">{what}</Text>
      </View>
    </View>
  );
}

// -- You -------------------------------------------------------------------------------------

/** You's Plus section (the You and YouPlus boards): the state, Widgets, and Restore or Manage. */
export function PlusSection({ status, plus, canManage = plus, actions }: {
  status: PlusStatus;
  plus: boolean;
  /** Somewhere to manage it (a store or daash.run). A grant from Daash has nowhere: no Manage row. */
  canManage?: boolean;
  actions: { open: () => void; widgets: () => void; restore: () => void; manage: () => void };
}) {
  const { colors } = useTheme();
  const row = { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 10, minHeight: 56, paddingVertical: 10, paddingHorizontal: 14 };
  const line = { borderTopWidth: 1, borderTopColor: colors.hairline };
  return (
    <View testID="you-plus" style={{ gap: 10 }}>
      <Text variant="kicker" tone="muted" style={{ marginTop: 6 }}>Plus</Text>
      <View style={{ borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, overflow: 'hidden' }}>
        <Pressable testID="row-plus" accessibilityRole="button" onPress={plus && canManage ? actions.manage : actions.open}
          accessibilityLabel={`${status.title}, ${status.sub}${status.badge ? `, ${status.badge}` : ''}`}
          accessibilityHint={plus && canManage ? 'Manage your subscription' : 'Opens Daash Plus'} style={row}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text variant="body">{status.title}</Text>
            <Text testID="plus-status" variant="footnote" tone="muted">{status.sub}</Text>
          </View>
          {status.badge ? (
            <View style={{ minHeight: 22, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6, justifyContent: 'center', backgroundColor: colors.sunken }}>
              <Text testID="plus-badge" variant="caption" tone="success" style={{ fontWeight: '700' }}>{status.badge}</Text>
            </View>
          ) : (
            <View style={{ paddingHorizontal: 12, minHeight: 32, borderRadius: 999, borderWidth: 1, borderColor: colors.muted, justifyContent: 'center' }}>
              <Text variant="subhead" style={{ fontWeight: '600' }}>Get Plus</Text>
            </View>
          )}
        </Pressable>
        <Pressable testID="row-widgets" accessibilityRole="button" onPress={actions.widgets}
          accessibilityLabel={plus ? 'Widgets, home and lock screen' : 'Widgets, Plus. Home and lock screen'} style={[row, line]}>
          {plus ? null : <Icon name="lock" tone="muted" />}
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text variant="body">Widgets</Text>
            <Text variant="footnote" tone="muted">Readiness, week and countdown</Text>
          </View>
          <Icon name="chevronRight" tone="muted" />
        </Pressable>
        {plus && !canManage ? null : (
          <Pressable testID={plus ? 'row-manage' : 'row-restore'} accessibilityRole="button" onPress={plus ? actions.manage : actions.restore} style={[row, line]}>
            <Text variant="body" style={{ flex: 1 }}>{plus ? 'Manage subscription' : 'Restore purchases'}</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}
