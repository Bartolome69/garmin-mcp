// The one inline watch status (build prompt, section 5.8). Each state is words plus
// an icon, never a colour alone. The full send flow arrives in milestone 5.
import { ActivityIndicator, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import type { WatchState } from '../../features/today/model';
import { Icon } from '../Icon';
import { Text } from '../Text';

export function watchWords(state: WatchState, at: string | null): string {
  switch (state) {
    case 'on_watch': return at ? `On your watch · ${at}` : 'On your watch';
    case 'changed_pending_sync': return 'Changed · arrives at next sync';
    case 'sending': return 'Sending';
    case 'failed': return 'Not sent';
    case 'confirming': return 'Waiting for your confirm';
    default: return 'Not on your watch';
  }
}

export function WatchStatus({ state, at }: { state: WatchState; at: string | null }) {
  const { colors } = useTheme();
  const words = watchWords(state, at);
  // A send that moves on is said once, by the watch queue (WatchProvider), not by each
  // status on screen: no live region here either, or TalkBack would say it again.
  const tone = state === 'on_watch' ? 'text2' : state === 'changed_pending_sync' ? 'warning'
    : state === 'failed' ? 'brandText' : 'muted';
  return (
    <View accessible accessibilityLabel={words} testID={`watch-${state}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      {state === 'sending' ? <ActivityIndicator size="small" color={colors.muted} />
        : <Icon name={state === 'failed' ? 'error' : state === 'changed_pending_sync' ? 'sync' : 'onWatch'}
          tone={state === 'on_watch' ? 'muted' : tone} />}
      <Text variant="footnote" tone={tone} numeric style={{ fontWeight: '500' }}>{words}</Text>
    </View>
  );
}
