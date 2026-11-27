// What a screen shows when its data isn't there or can't be refreshed (build prompt,
// sections 3 and 7): a skeleton while the first answer comes (never a spinner over
// content), and on failure what happened and the one thing to do about it. A Garmin
// login that expired, or a signed-out app, is said plainly with its own button, so
// nothing waits forever on an answer that can't come.
import { Linking, View } from 'react-native';

import { API_BASE } from '../api/client';
import type { FailureKind } from '../data/resource';
import { useTheme } from '../theme/ThemeProvider';
import { Button } from './Button';
import { Icon } from './Icon';
import { Text } from './Text';

/** Garmin's own sign-in, on the server. TODO(server): back to the app with a token (milestone 9). */
export function openGarminSignIn(): void {
  Linking.openURL(`${API_BASE}/connect?app=native`).catch(() => {});
}

export const FAILURE_WORDS: Record<FailureKind, { title: (what: string) => string; body: string; action: string }> = {
  offline: { title: (what) => `${what[0].toUpperCase()}${what.slice(1)} isn’t on this phone yet`, body: 'You’re offline. It loads as soon as you’re back online.', action: 'Try again' },
  reconnect: { title: () => 'Garmin needs you to sign in again', body: 'Your plan is safe. Sign in on Garmin’s page and Daash picks up where it left off.', action: 'Reconnect' },
  signedOut: { title: () => 'You’re signed out', body: 'Sign in to see your plan and runs.', action: 'Sign in' },
  server: { title: (what) => `Couldn’t load ${what}`, body: 'Daash didn’t answer. Try again in a moment.', action: 'Try again' },
};

/** The skeleton while the first answer comes: blocks where the cards will be. */
export function Skeleton({ testID, label, blocks = [140, 170, 110] }: { testID: string; label: string; blocks?: number[] }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} accessible accessibilityLabel={label} style={{ flex: 1, backgroundColor: colors.bg, padding: 16, gap: 10 }}>
      {blocks.map((h, i) => <View key={i} style={{ height: h, borderRadius: 12, backgroundColor: colors.surface }} />)}
    </View>
  );
}

/** No data to show: loading (a skeleton), or why not and what to do. */
export function LoadState({ what, failure, error, onRetry, testID }: {
  what: string; failure: FailureKind | null; error: string | null; onRetry: () => void; testID: string;
}) {
  const { colors } = useTheme();
  if (!error && !failure) return <Skeleton testID={`${testID}-loading`} label={`Loading ${what}`} />;
  const kind = failure ?? 'server';
  const w = FAILURE_WORDS[kind];
  const act = kind === 'reconnect' || kind === 'signedOut' ? openGarminSignIn : onRetry;
  return (
    <View testID={`${testID}-error`} style={{ flex: 1, backgroundColor: colors.bg, padding: 16, gap: 12, justifyContent: 'center' }}>
      <Text variant="headline" accessibilityRole="header">{w.title(what)}</Text>
      <Text variant="body" tone="muted">{kind === 'server' && error ? error : w.body}</Text>
      <Button label={w.action} kind={kind === 'reconnect' || kind === 'signedOut' ? 'primary' : 'secondary'} onPress={act} />
    </View>
  );
}

/** Over cached content: Garmin's login expired, or the app is signed out. Offline is the "Updated …" label's job. */
export function FailureBanner({ failure }: { failure: FailureKind | null }) {
  const { colors } = useTheme();
  if (failure !== 'reconnect' && failure !== 'signedOut') return null;
  const w = FAILURE_WORDS[failure];
  return (
    <View testID="failure-banner" accessibilityLiveRegion="polite"
      style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginHorizontal: 16, marginTop: 8, padding: 12, borderRadius: 12,
        backgroundColor: colors.warningTint, borderWidth: 1, borderColor: colors.warning }}>
      <Icon name="warning" tone="warning" />
      <View style={{ flex: 1 }}>
        <Text variant="subhead" style={{ fontWeight: '600' }}>{w.title('')}</Text>
        <Text variant="footnote" tone="muted">Showing what’s on your phone.</Text>
      </View>
      <Button label={w.action} small onPress={openGarminSignIn} />
    </View>
  );
}
