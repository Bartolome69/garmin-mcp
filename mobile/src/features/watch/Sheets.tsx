// The watch confirm (build prompt, sections 1 and 5.4): nothing is written to the
// watch without this sheet listing exactly what changes. One component, three
// shapes: a batch of changes ("3 changes on your watch"), one session ("Send to
// Forerunner 965?") and a removal ("Remove from your watch?"). Also the Life
// happened sheet and the one toast after a send.
import { useState } from 'react';
import { Pressable, View } from 'react-native';

import { DAYS, parseDate, weekday } from '../../domain/format';
import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import { VERB, type Proposal } from './model';
import { MEDICAL, REASONS, sinceChoices, type Reason } from './proposals';
import type { Connection } from './transport';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const longDay = (iso: string) => { const { m, d } = parseDate(iso); return `${DAYS[weekday(iso)]} ${d} ${MONTHS[m - 1]}`; };

export function connectionWords(c: Connection): string {
  return `Connected to ${c.provider}${c.syncedAt ? ` · synced ${c.syncedAt}` : ''}`;
}

function List({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  return <View testID="confirm-list" style={{ borderRadius: 12, backgroundColor: colors.sunken, paddingHorizontal: 12 }}>{children}</View>;
}

function Line({ first, children, label }: { first: boolean; children: React.ReactNode; label: string }) {
  const { colors } = useTheme();
  return (
    <View accessible accessibilityLabel={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 40, paddingVertical: 8,
      borderTopWidth: first ? 0 : 1, borderTopColor: colors.hairline }}>
      {children}
    </View>
  );
}

export function ConfirmSheet({ p, connection, onConfirm, onBack }: {
  p: Proposal; connection: Connection; onConfirm: () => void; onBack: () => void;
}) {
  const n = p.ops.length;
  const watch = connection.device ?? 'your watch';
  if (p.source === 'unwatch') {
    return (
      <View testID="confirm-sheet" style={{ padding: 20, gap: 12 }}>
        <Text variant="title" accessibilityRole="header">Remove from your watch?</Text>
        <List>
          {p.ops.map((o, i) => (
            <Line key={i} first={i === 0} label={`Remove, ${o.dayLabel}, ${o.what}`}>
              <Text variant="subhead" style={{ flex: 1 }}>
                <Text variant="subhead" style={{ fontWeight: '600' }}>Remove</Text>{` · ${longDay(o.date)} · ${o.what}`}
              </Text>
            </Line>
          ))}
        </List>
        <Text variant="footnote" tone="muted">It stays in your plan, marked Not on watch. You can send it again any time.</Text>
        <Button label="Remove from watch" kind="primary" onPress={onConfirm} />
        <Button label="Keep it" onPress={onBack} />
      </View>
    );
  }
  if (p.source === 'build') {
    // From the builder: one session, added or replacing the one edited (off its old day if it moved).
    const o = p.ops.find((x) => x.kind !== 'remove') ?? p.ops[0];
    const off = p.ops.find((x) => x.kind === 'remove');
    const size = o.after?.sub.split(' · ')[1] ?? '';
    const replaces = o.kind === 'replace' ? o.what : off ? `${off.what} on ${longDay(off.date)}` : 'nothing';
    return (
      <View testID="confirm-sheet" style={{ padding: 20, gap: 12 }}>
        <Text variant="title" accessibilityRole="header">{`Send to ${watch}?`}</Text>
        <List>
          <Line first label={`${longDay(o.date)}, ${p.title}${size ? `, ${size}` : ''}`}>
            <Text variant="subhead" style={{ flex: 1, fontWeight: '600' }}>{`${longDay(o.date)} · ${p.title}`}</Text>
            <Text variant="subhead" tone="muted" numeric>{size}</Text>
          </Line>
          <Line first={false} label={`Targets, ${p.subtitle}`}>
            <Text variant="subhead" style={{ flex: 1 }}>Targets</Text>
            <Text variant="subhead" tone="muted" numeric>{p.subtitle}</Text>
          </Line>
          <Line first={false} label={`Replaces, ${replaces}`}>
            <Text variant="subhead" style={{ flex: 1 }}>Replaces</Text>
            <Text variant="subhead" tone="muted" style={{ flexShrink: 1, textAlign: 'right' }}>{replaces}</Text>
          </Line>
        </List>
        <Text variant="footnote" tone="muted">{`Connected to ${connection.provider}. It goes into your plan and shows up on the watch next time it syncs with your phone.`}</Text>
        <Button label="Confirm and send" kind="primary" onPress={onConfirm} />
        <Button label="Not now" onPress={onBack} />
      </View>
    );
  }
  if (p.source === 'send' || p.source === 'adjust') {
    const o = p.ops[0];
    const size = (o.after ?? o.before)?.sub.split(' · ')[1] ?? '';
    return (
      <View testID="confirm-sheet" style={{ padding: 20, gap: 12 }}>
        <Text variant="title" accessibilityRole="header">{`Send to ${watch}?`}</Text>
        <List>
          <Line first label={`${longDay(o.date)}, ${o.what}${size ? `, ${size}` : ''}`}>
            <Text variant="subhead" style={{ flex: 1, fontWeight: '600' }}>{`${longDay(o.date)} · ${o.withWhat ? o.what.replace(/, \d+% (easier|harder)$/, '') : o.what}`}</Text>
            <Text variant="subhead" tone="muted" numeric>{size}</Text>
          </Line>
          <Line first={false} label={`Targets, ${p.subtitle}`}>
            <Text variant="subhead" style={{ flex: 1 }}>Targets</Text>
            <Text variant="subhead" tone="muted">{p.subtitle}</Text>
          </Line>
          <Line first={false} label={`Replaces, ${o.kind === 'replace' ? 'the version on your watch' : 'nothing'}`}>
            <Text variant="subhead" style={{ flex: 1 }}>Replaces</Text>
            <Text variant="subhead" tone="muted">{o.kind === 'replace' ? 'the version on your watch' : 'nothing'}</Text>
          </Line>
        </List>
        <Text variant="footnote" tone="muted">{`Connected to ${connection.provider}. It shows up on the watch next time it syncs with your phone.`}</Text>
        <Button label="Confirm and send" kind="primary" onPress={onConfirm} />
        <Button label="Not now" onPress={onBack} />
      </View>
    );
  }
  return (
    <View testID="confirm-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">{`${n} change${n === 1 ? '' : 's'} on your watch`}</Text>
      <Text variant="footnote" tone="muted">{`This is exactly what ${watch} gets. Nothing else on the watch changes.`}</Text>
      <List>
        {p.ops.map((o, i) => (
          <Line key={i} first={i === 0} label={`${VERB[o.kind]}, ${o.dayLabel}, ${o.what}${o.withWhat ? `, with ${o.withWhat}` : ''}`}>
            <Text variant="subhead" style={{ flex: 1 }} testID={`confirm-op-${i}`}>
              <Text variant="subhead" style={{ fontWeight: '600' }}>{VERB[o.kind]}</Text>
              {` · ${o.dayLabel} · ${o.what}`}
              {o.withWhat ? <Text variant="subhead" tone="muted">{` with ${o.withWhat}`}</Text> : null}
            </Text>
          </Line>
        ))}
      </List>
      <View accessible accessibilityLabel={connectionWords(connection)} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <Icon name="onWatch" tone="muted" />
        <Text variant="footnote" tone="muted">{connectionWords(connection)}</Text>
      </View>
      <Button label={`Confirm and send ${n} change${n === 1 ? '' : 's'}`} kind="primary" onPress={onConfirm} />
      <Button label="Back" onPress={onBack} />
    </View>
  );
}

/** Life happened: what changed, and since when. Nothing changes until you confirm. */
export function LifeSheet({ today, busy, onSee }: { today: string; busy: boolean; onSee: (reason: Reason, since: string) => void }) {
  const { colors } = useTheme();
  const [reason, setReason] = useState<Reason>('unwell');
  const [since, setSince] = useState(today);
  const [more, setMore] = useState(false);
  const choices = sinceChoices(today);
  const chip = (c: { date: string; label: string }) => {
    const on = c.date === since;
    return (
      <Pressable key={c.date} testID={`since-${c.date}`} accessibilityRole="radio" accessibilityState={{ selected: on }}
        onPress={() => setSince(c.date)} style={{ minHeight: MIN_TOUCH, justifyContent: 'center' }}>
        <View style={{ minHeight: 32, paddingHorizontal: 14, borderRadius: 999, justifyContent: 'center',
          backgroundColor: on ? colors.text : 'transparent', borderWidth: 1, borderColor: on ? colors.text : colors.muted }}>
          <Text variant="subhead" tone={on ? 'bg' : 'text'} style={{ fontWeight: '600' }}>{c.label}</Text>
        </View>
      </Pressable>
    );
  };
  return (
    <View testID="life-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">What changed?</Text>
      <Text variant="footnote" tone="muted">Pick one and Daash proposes a new week. Nothing changes until you confirm.</Text>
      <View accessibilityRole="radiogroup" style={{ borderRadius: 12, backgroundColor: colors.sunken, paddingHorizontal: 12 }}>
        {REASONS.map((r, i) => {
          const on = r.key === reason;
          return (
            <Pressable key={r.key} testID={`reason-${r.key}`} accessibilityRole="radio" accessibilityState={{ selected: on }}
              accessibilityLabel={`${r.title}. ${r.sub}`} onPress={() => setReason(r.key)}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 56, paddingVertical: 8,
                borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline }}>
              <View style={{ flex: 1 }}>
                <Text variant="headline">{r.title}</Text>
                <Text variant="footnote" tone="muted">{r.sub}</Text>
              </View>
              <View style={{ width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: on ? colors.text : colors.muted,
                backgroundColor: on ? colors.text : 'transparent', alignItems: 'center', justifyContent: 'center' }}>
                {on ? <Icon name="check" tone="bg" /> : null}
              </View>
            </Pressable>
          );
        })}
      </View>
      <View style={{ gap: 4 }}>
        <Text variant="footnote" tone="muted">Since</Text>
        <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 8 }}>
          {choices.slice(0, 2).map(chip)}
          {more ? choices.slice(2).map(chip) : (
            <Pressable testID="since-pick" accessibilityRole="button" onPress={() => setMore(true)} style={{ minHeight: MIN_TOUCH, justifyContent: 'center' }}>
              <View style={{ minHeight: 32, paddingHorizontal: 14, borderRadius: 999, justifyContent: 'center', borderWidth: 1, borderColor: colors.muted }}>
                <Text variant="subhead" style={{ fontWeight: '600' }}>{choices.some((c, i) => i > 1 && c.date === since) ? choices.find((c) => c.date === since)!.label : 'Pick a date'}</Text>
              </View>
            </Pressable>
          )}
        </View>
      </View>
      <Button label={busy ? 'Working it out…' : 'See the new week'} kind="primary" disabled={busy} onPress={() => onSee(reason, since)} />
      <Text variant="footnote" tone="muted">{MEDICAL}</Text>
    </View>
  );
}

/** The only toast after a send (build prompt, section 5.8). */
export function SendToast({ onRemove, onDismiss }: { onRemove: () => void; onDismiss: () => void }) {
  const { colors } = useTheme();
  return (
    <View testID="send-toast" accessibilityLiveRegion="polite" style={{ position: 'absolute', left: 16, right: 16, bottom: 100,
      flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 14, borderRadius: 12, backgroundColor: colors.surface,
      borderWidth: 1, borderColor: colors.hairline, elevation: 6 }}>
      <Text variant="subhead" tone="text2" style={{ flex: 1 }}>Changed your mind?</Text>
      <Pressable testID="toast-remove" accessibilityRole="button" onPress={onRemove} style={{ minHeight: 48, justifyContent: 'center', paddingHorizontal: 8 }}>
        <Text variant="subhead" style={{ fontWeight: '600', textDecorationLine: 'underline' }}>Remove from watch</Text>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Dismiss" onPress={onDismiss} style={{ minHeight: 48, minWidth: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
        <Icon name="close" tone="muted" />
      </Pressable>
    </View>
  );
}

