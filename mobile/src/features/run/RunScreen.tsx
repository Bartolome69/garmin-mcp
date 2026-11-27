// Post-run as a screen in a tab's stack (Today's week strip, Plan's done rows):
// the run, Share on the right (iOS words, Android an icon), and "Ask about this
// run" into the Coach with the run as its context.
import { Stack, useRouter } from 'expo-router';
import { Platform, Pressable, Share } from 'react-native';

import { useFlags } from '../../flags/FlagsProvider';
import { MIN_TOUCH } from '../../ui/Button';
import { LoadState } from '../../ui/StateViews';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import { RunView, shareText } from './RunView';
import { useRun } from './useRun';

export function RunScreen({ id, fetcher }: { id: string; fetcher?: Parameters<typeof useRun>[2] }) {
  const { flags } = useFlags();
  const router = useRouter();
  const run = useRun(id, flags.coach_enabled, fetcher);
  const vm = run.data;
  const share = () => { if (vm) Share.share({ message: shareText(vm) }).catch(() => {}); };
  return (
    <>
      <Stack.Screen options={{ title: '', headerRight: vm ? () => (
        <Pressable testID="share" accessibilityRole="button" accessibilityLabel="Share" onPress={share}
          style={{ minHeight: MIN_TOUCH, minWidth: MIN_TOUCH, alignItems: 'center', justifyContent: 'center' }}>
          {Platform.OS === 'ios' ? <Text variant="body">Share</Text> : <Icon name="share" context="toolbar" />}
        </Pressable>
      ) : undefined }} />
      {vm ? (
        <RunView vm={vm} coach={flags.coach_enabled}
          onAsk={() => router.push({ pathname: '/coach', params: { run: vm.sessionId ?? vm.id } })} />
      ) : (
        <LoadState what="this run" failure={run.failure} error={run.error} onRetry={run.refresh} testID="run-missing" />
      )}
    </>
  );
}
