// A session's watch status where you act on it (build prompt, section 5.8): the
// state appears once, in this row, and every write ends in one you can see.
//
//   Not on your watch          [Send to watch]
//   ◌ Sending to Forerunner 965                  now
//   ⟳ Changed · arrives at next sync
//   ⌚ On your watch                            20:14   [Remove from watch]
//   ⚠ Not sent. Garmin didn't answer.            20:14
//     [Resend] [Trouble connecting?]
//     Nothing changed on your watch. Daash retries when Garmin is back.
import { ActivityIndicator, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Icon } from '../../ui/Icon';
import { WatchStatus } from '../../ui/session/WatchStatus';
import { Text } from '../../ui/Text';
import type { Watch } from '../plan/model';

export function WatchCard({ watch, device, error, retrying, adjusted, brand = true, onSend, onResend, onRemove, onTrouble }: {
  watch: Watch;
  device: string | null;
  /** The failure in words, from the queue. */
  error: string | null;
  /** A failure Daash will try again on its own. */
  retrying: boolean;
  /** The targets changed here (Easier / Harder) since it went to the watch. */
  adjusted: boolean;
  brand?: boolean;
  onSend: () => void;
  onResend: () => void;
  onRemove: () => void;
  onTrouble: () => void;
}) {
  const { colors } = useTheme();
  const name = device ?? 'your watch';
  const { state, at } = watch;

  if (state === 'failed') {
    return (
      <View testID="watch-card" style={{ gap: 8 }}>
        <View accessible accessibilityLiveRegion="polite" accessibilityLabel={`${error ?? 'Not sent.'}${at ? ` ${at}` : ''}`}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderRadius: 12, backgroundColor: colors.sunken }}>
          <Icon name="warning" tone="brandText" />
          <Text variant="subhead" tone="brandText" style={{ flex: 1, fontWeight: '600' }}>{error ?? 'Not sent.'}</Text>
          {at ? <Text variant="stepTarget" tone="muted" numeric>{at}</Text> : null}
        </View>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Button label="Resend" kind="primary" brand={brand} small onPress={onResend} />
          <Button label="Trouble connecting?" small onPress={onTrouble} />
        </View>
        <Text variant="footnote" tone="muted">
          {`Nothing changed on your watch.${retrying ? ' Daash retries when Garmin is back.' : ''}`}
        </Text>
      </View>
    );
  }

  if (state === 'sending') {
    return (
      <Card testID="watch-card" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <View accessible accessibilityLiveRegion="polite" accessibilityLabel={`Sending to ${name}`}
          style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <ActivityIndicator size="small" color={colors.muted} />
          <Text variant="subhead" tone="muted" style={{ flex: 1 }}>{`Sending to ${name}`}</Text>
        </View>
        <Text variant="stepTarget" tone="muted">now</Text>
      </Card>
    );
  }

  const onWatch = state === 'on_watch' || state === 'changed_pending_sync';
  return (
    <Card testID="watch-card" style={{ gap: 8 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <View style={{ flexShrink: 1, gap: 2 }}>
          <WatchStatus state={state} at={at} />
          {adjusted && onWatch ? <Text variant="footnote" tone="muted">The watch has the old targets until you send.</Text> : null}
        </View>
        {!onWatch || adjusted
          ? <Button label={adjusted && onWatch ? 'Send changes' : 'Send to watch'} kind="primary" brand={brand} small onPress={onSend} />
          : <Button label="Remove from watch" small onPress={onRemove} />}
      </View>
    </Card>
  );
}
