// Connections, the moat (build prompt, section 5.12): one card per provider with a
// generic watch glyph, the name, a state badge (Live / Via Intervals.icu / via the
// phone's health store / Coming soon), the last sync and capability chips, ticked
// or struck through. Trust states: Reconnect needed (icon, words and "Reconnect"),
// "What Daash can and can't see", "Trouble connecting?", "Don't see your device?
// Request it". Source priority: drag to order; merges listed with "These are
// different runs". Advanced merge rules are Plus: shown, locked.
import { useRef, useState } from 'react';
import { PanResponder, Platform, Pressable, ScrollView, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Icon } from '../../ui/Icon';
import { PlusRow } from '../../ui/Locked';
import { COMING_NOTE } from '../plus/coming';
import { openGarminSignIn } from '../../ui/StateViews';
import { Text } from '../../ui/Text';
import { badgeWords, mergeTitle, mergeWords, prioritySummary, sourceName, youSummary, type ConnectionsVM, type Provider } from './model';
import { READABLE } from '../../ui/Screen';

export const DISCLAIMER = '“Connected to” means you linked your own account. Daash is independent and not endorsed by any watch maker.';

function Badge({ p, os }: { p: Provider; os: string }) {
  const { colors } = useTheme();
  const tint = p.route === 'direct' ? colors.success : colors.cLong;
  return (
    <View style={{ minHeight: 22, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6, justifyContent: 'center', overflow: 'hidden' }}>
      <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: tint, opacity: 0.18 }} />
      <Text variant="caption" tone={p.route === 'direct' ? 'success' : 'text'} style={{ fontWeight: '700' }}>{badgeWords(p, os)}</Text>
    </View>
  );
}

function Tile() {
  const { colors } = useTheme();
  return (
    <View style={{ width: 40, height: 40, borderRadius: 10, backgroundColor: colors.sunken, alignItems: 'center', justifyContent: 'center' }}>
      <Icon name="onWatch" tone="text2" />
    </View>
  );
}

export function ProviderCard({ p, os, onReconnect }: { p: Provider; os: string; onReconnect: (p: Provider) => void }) {
  const { colors } = useTheme();
  const reconnect = p.state === 'reconnect';
  return (
    <View testID={`provider-${p.key}`} style={{ gap: 10, padding: 14, borderRadius: os === 'android' ? 12 : 16, backgroundColor: colors.surface,
      borderWidth: 1, borderColor: colors.hairline }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <Tile />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }} accessible accessibilityLabel={`${p.name}, ${badgeWords(p, os)}, ${p.status}`}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Text variant="headline">{p.name}</Text>
            <Badge p={p} os={os} />
          </View>
          {reconnect ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Icon name="warning" tone="warning" />
              <Text testID={`status-${p.key}`} variant="footnote" tone="warning" style={{ flexShrink: 1 }}>{p.status}</Text>
            </View>
          ) : <Text testID={`status-${p.key}`} variant="footnote" tone="muted" numeric>{p.status}</Text>}
        </View>
        {reconnect ? <Button label="Reconnect" small onPress={() => onReconnect(p)} /> : null}
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 12, rowGap: 4 }}>
        {p.caps.map((c) => (
          <View key={c.label} accessible accessibilityLabel={`${c.label}, ${c.on ? 'yes' : 'no'}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <Icon name={c.on ? 'check' : 'close'} tone={c.on ? 'text' : 'muted'} />
            <Text variant="caption" tone={c.on ? 'text' : 'muted'} style={c.on ? undefined : { textDecorationLine: 'line-through' }}>{c.label}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

function Row({ title, sub, right, onPress, chevron = false, testID }: {
  title: string; sub?: string; right?: React.ReactNode; onPress?: () => void; chevron?: boolean; testID?: string;
}) {
  const { colors } = useTheme();
  const body = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 56, paddingVertical: 10, paddingHorizontal: 14 }}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="body">{title}</Text>
        {sub ? <Text variant="footnote" tone="muted">{sub}</Text> : null}
      </View>
      {right}
      {chevron ? <Icon name="chevronRight" tone="muted" /> : null}
    </View>
  );
  return onPress
    ? <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={sub ? `${title}, ${sub}` : title} onPress={onPress}
        style={({ pressed }) => ({ backgroundColor: pressed ? colors.sunken : 'transparent' })}>{body}</Pressable>
    : <View testID={testID}>{body}</View>;
}

function List({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  const items = (Array.isArray(children) ? children : [children]).filter(Boolean);
  return (
    <View style={{ borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, overflow: 'hidden' }}>
      {items.map((c, i) => <View key={i} style={{ borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline }}>{c}</View>)}
    </View>
  );
}

function Link({ label, onPress, testID }: { label: string; onPress: () => void; testID: string }) {
  return (
    <Pressable testID={testID} accessibilityRole="link" onPress={onPress} style={{ minHeight: MIN_TOUCH, justifyContent: 'center' }}>
      <Text variant="footnote" style={{ textDecorationLine: 'underline' }}>{label}</Text>
    </Pressable>
  );
}

export type ConnectionsActions = {
  reconnect: (p: Provider) => void; priority: () => void; see: () => void; trouble: () => void; request: () => void;
  tell: (what: string) => void;
};

export function ConnectionsView({ vm, asked, actions, os = Platform.OS, updated = null }: {
  vm: ConnectionsVM; asked: string[]; actions: ConnectionsActions; os?: string;
  /** "Updated 2h ago" when what's shown is the phone's copy and it's old. */
  updated?: string | null;
}) {
  const { colors } = useTheme();
  const coming = vm.coming.join(', ');
  const told = asked.includes(`tell:${coming}`);
  return (
    <ScrollView testID="connections" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, padding: 16, gap: 10 }}>
      {updated ? <Text testID="connections-updated" variant="footnote" tone="muted">{updated}</Text> : null}
      {vm.providers.map((p) => <ProviderCard key={p.key} p={p} os={os} onReconnect={actions.reconnect} />)}
      {!vm.providers.length ? (
        <Card testID="connections-empty" style={{ gap: 8 }}>
          <Text variant="headline">Nothing connected yet</Text>
          <Text variant="footnote" tone="muted">Connect your watch and Daash reads your runs, sleep and readiness, and sends the sessions you confirm.</Text>
          <Button label="Connect Garmin" kind="primary" onPress={openGarminSignIn} />
        </Card>
      ) : null}
      <List>
        <Row testID="row-priority" title="Source priority" sub={prioritySummary(vm, os)} onPress={actions.priority} chevron />
        {coming ? (
          <Row testID="row-coming" title={coming} sub="Coming soon"
            right={<Button label={told ? 'We’ll tell you' : 'Tell me when'} small disabled={told} onPress={() => actions.tell(`tell:${coming}`)} />} />
        ) : null}
        <Row testID="row-request" title="Don’t see your device?" right={<Button label="Request it" small onPress={actions.request} />} />
      </List>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', flexWrap: 'wrap', columnGap: 12 }}>
        <Link testID="link-see" label="What Daash can and can’t see" onPress={actions.see} />
        <Link testID="link-trouble" label="Trouble connecting?" onPress={actions.trouble} />
      </View>
      <Text variant="footnote" tone="muted">{DISCLAIMER}</Text>
    </ScrollView>
  );
}

// -- You -----------------------------------------------------------------------------------

/** You (build prompt, section 4): a provider needing a reconnect comes first, with the screen's one brand fill. */
export function YouView({ vm, brand, actions, children, plus, top, settings }: {
  vm: ConnectionsVM | null; brand: boolean; actions: { connections: () => void; reconnect: (p: Provider) => void };
  /** You's other sections (the Coach and Training preferences, milestone 11), after Connections. */
  children?: React.ReactNode;
  /** You's Plus section (the YouPlus board). */
  plus?: React.ReactNode;
  /** Above everything: the "Sample data" label when it shows. */
  top?: React.ReactNode;
  /** Activities, settings, privacy, help and Sign out, last (milestone 12). */
  settings?: React.ReactNode;
}) {
  const { colors } = useTheme();
  const broken = vm?.providers.filter((p) => p.state === 'reconnect') ?? [];
  return (
    <ScrollView testID="you" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, padding: 16, gap: 10 }}>
      {top}
      {broken.map((p) => (
        <Card key={p.key} testID={`trust-${p.key}`} style={{ gap: 8, borderWidth: 1, borderColor: colors.warning }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <Tile />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text variant="headline">{p.name}</Text>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Icon name="warning" tone="warning" />
                <Text variant="footnote" tone="warning" style={{ flexShrink: 1 }}>{p.status}</Text>
              </View>
            </View>
          </View>
          <Text variant="footnote" tone="muted">Your plan is safe. Runs from here wait until it’s back.</Text>
          <Button label="Reconnect" kind="primary" brand={brand} onPress={() => actions.reconnect(p)} />
        </Card>
      ))}
      <Text variant="kicker" accessibilityRole="header" tone="muted" style={{ marginTop: 6 }}>Connections</Text>
      <List>
        <Row testID="row-connections" title="Watches and apps" sub={vm ? youSummary(vm) : 'Not loaded yet'} onPress={actions.connections} chevron />
      </List>
      {/* The prompt's order (section 4): Connections, then Coach memory or Training preferences, then Plus. */}
      {children}
      {plus}
      {settings}
    </ScrollView>
  );
}

// -- Source priority -------------------------------------------------------------------------

const ROW_H = 64;

function PriorityRow({ index, count, title, sub, onMove }: { index: number; count: number; title: string; sub: string; onMove: (from: number, to: number) => void }) {
  const { colors } = useTheme();
  const [dy, setDy] = useState(0);
  const latest = useRef({ index, count, onMove });
  latest.current = { index, count, onMove };
  const pan = useRef(PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: () => true,
    onPanResponderMove: (_, g) => setDy(g.dy),
    onPanResponderRelease: (_, g) => {
      const { index: i, count: n, onMove: move } = latest.current;
      const to = Math.max(0, Math.min(n - 1, i + Math.round(g.dy / ROW_H)));
      setDy(0);
      if (to !== i) move(i, to);
    },
    onPanResponderTerminate: () => setDy(0),
  })).current;
  return (
    <View testID={`source-${index}`} accessible accessibilityLabel={`${index + 1}, ${title}, ${sub}`}
      accessibilityHint="Swipe up or down to move it"
      accessibilityActions={[...(index > 0 ? [{ name: 'moveUp', label: 'Move up' }] : []), ...(index < count - 1 ? [{ name: 'moveDown', label: 'Move down' }] : [])]}
      onAccessibilityAction={(e) => onMove(index, index + (e.nativeEvent.actionName === 'moveUp' ? -1 : 1))}
      style={{ minHeight: ROW_H, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, backgroundColor: colors.surface,
        transform: [{ translateY: dy }], zIndex: dy ? 2 : 0, borderTopWidth: index ? 1 : 0, borderTopColor: colors.hairline }}>
      <Text variant="kicker" style={{ width: 16, textTransform: 'none' }} numeric>{String(index + 1)}</Text>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="body">{title}</Text>
        <Text variant="footnote" tone="muted">{sub}</Text>
      </View>
      <View testID={`grip-${index}`} {...pan.panHandlers} style={{ width: MIN_TOUCH, height: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
        <Icon name="grip" tone="muted" />
      </View>
    </View>
  );
}

export function PriorityView({ vm, os = Platform.OS, plus, actions }: {
  vm: ConnectionsVM; os?: string; plus: boolean;
  actions: { reorder: (order: string[]) => void; keepApart: (id: string) => void; mergeAgain: (pair: [string, string]) => void; plus: () => void };
}) {
  const { colors } = useTheme();
  const sources = vm.priority.map((k) => vm.providers.find((p) => p.key === k)!).filter(Boolean);
  const move = (from: number, to: number) => {
    const order = [...vm.priority];
    const [k] = order.splice(from, 1);
    order.splice(to, 0, k);
    actions.reorder(order);
  };
  const kept = (key: string) => { const p = vm.providers.find((x) => x.key === key); return p ? sourceName(p, os) : key; };
  return (
    <ScrollView testID="priority" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, padding: 16, gap: 12 }}>
      <Text variant="footnote" tone="muted">When two sources send the same run, Daash keeps the one higher on this list and hides the other. Drag to reorder.</Text>
      <View testID="priority-list" style={{ borderRadius: 12, borderWidth: 1, borderColor: colors.hairline, overflow: 'hidden' }}>
        {sources.map((p, i) => <PriorityRow key={p.key} index={i} count={sources.length} title={sourceName(p, os)} sub={p.reads} onMove={move} />)}
      </View>
      <Text variant="kicker" accessibilityRole="header" tone="muted" style={{ marginTop: 6 }}>Merged this month</Text>
      {vm.merges.length ? vm.merges.map((m) => (
        <Card key={m.id} testID={`merge-${m.id}`} style={{ gap: 8 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <Text variant="headline" numeric>{m.title}</Text>
            <View style={{ minHeight: 22, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 6, justifyContent: 'center', backgroundColor: colors.sunken }}>
              <Text variant="caption" tone="muted" style={{ fontWeight: '700' }}>{`Kept ${kept(m.keptProvider)}`}</Text>
            </View>
          </View>
          <Text variant="footnote" tone="muted" numeric>{mergeWords(m, vm, os)}</Text>
          <View style={{ alignSelf: 'flex-start' }}>
            <Button label="These are different runs" small onPress={() => actions.keepApart(m.id)} />
          </View>
        </Card>
      )) : <Text testID="no-merges" variant="footnote" tone="muted">No runs came twice this month.</Text>}
      {vm.keptApart.length ? (
        <>
          <Text variant="kicker" accessibilityRole="header" tone="muted" style={{ marginTop: 6 }}>Kept apart</Text>
          {vm.keptApart.map((pair) => (
            <Card key={pair.join('|')} testID={`apart-${pair.join('|')}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Text variant="subhead" style={{ flex: 1 }} numeric>{`${mergeTitle(pair.join('|'), vm)} · both shown`}</Text>
              <Button label="Merge them again" small onPress={() => actions.mergeAgain(pair)} />
            </Card>
          ))}
        </>
      ) : null}
      <PlusRow testID="advanced" title="Advanced merge rules" plus={plus} onPress={actions.plus}
        sub={`Pick the source per metric, tune match windows. ${COMING_NOTE}. Now: within ${vm.match.startWithinSeconds / 60 === 1 ? '1 minute' : `${vm.match.startWithinSeconds} s`} and ±${vm.match.distanceWithinPct}%.`} />
    </ScrollView>
  );
}

// -- trust sheets -----------------------------------------------------------------------------

export function SeeSheet({ vm, os = Platform.OS }: { vm: ConnectionsVM; os?: string }) {
  return (
    <ScrollView testID="see-sheet" contentContainerStyle={{ ...READABLE, padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">What Daash can and can’t see</Text>
      {vm.providers.map((p) => {
        const can = p.caps.filter((c) => c.on).map((c) => c.label.toLowerCase());
        const cant = p.caps.filter((c) => !c.on).map((c) => c.label.toLowerCase());
        return (
          <View key={p.key} testID={`see-${p.key}`} style={{ gap: 2 }}>
            <Text variant="headline">{sourceName(p, os)}</Text>
            <Text variant="footnote">{`Can: ${can.join(', ') || 'nothing yet'}.`}</Text>
            {cant.length ? <Text variant="footnote" tone="muted">{`Can’t: ${cant.join(', ')}.`}</Text> : null}
          </View>
        );
      })}
      <Text variant="footnote" tone="muted">Daash only reads what you connect, only to plan and explain your training, and only puts a workout on your watch after you confirm it. Nothing is sold or shared. Disconnecting stops new data straight away.</Text>
    </ScrollView>
  );
}

export function TroubleSheet({ vm, onReconnect }: { vm: ConnectionsVM; onReconnect: (p: Provider) => void }) {
  const broken = vm.providers.filter((p) => p.state === 'reconnect');
  return (
    <ScrollView testID="trouble-sheet" contentContainerStyle={{ ...READABLE, padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">Trouble connecting?</Text>
      {[
        'Open your watch’s own app and check the watch has synced there. Daash only sees what reaches it.',
        'If a card here says Reconnect needed, reconnect it. Your plan and sessions are kept while it’s off.',
        'A workout you sent shows on the watch after its next sync with your phone.',
        'Still stuck? Disconnect and connect again from this screen.',
      ].map((t, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: 10 }}>
          <Text variant="subhead" style={{ fontWeight: '700', width: 16 }} numeric>{String(i + 1)}</Text>
          <Text variant="subhead" style={{ flex: 1 }}>{t}</Text>
        </View>
      ))}
      {broken.map((p) => <Button key={p.key} label={`Reconnect ${p.name}`} onPress={() => onReconnect(p)} />)}
    </ScrollView>
  );
}
