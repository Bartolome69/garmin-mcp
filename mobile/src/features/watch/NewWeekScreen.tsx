// The "New week" screen, in Plan's and Today's stacks: the shared diff for a staged
// proposal. It goes back by itself once the proposal is confirmed.
import { useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';

import { useFlags } from '../../flags/FlagsProvider';
import { Screen } from '../../ui/Screen';
import { Text } from '../../ui/Text';
import { DiffView } from './DiffView';
import { useWatch } from './WatchProvider';

export function NewWeekScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const watch = useWatch();
  const { flags } = useFlags();
  const router = useRouter();
  const focused = useIsFocused();
  const p = watch.draft(id);
  const confirmed = !p && watch.ops.some((o) => o.batch.startsWith(`${id}@`));

  useEffect(() => { if (confirmed && focused && router.canGoBack()) router.back(); }, [confirmed, focused, router]);

  if (!p) {
    return <Screen testID="diff-gone"><Text variant="body" tone="muted">{confirmed ? 'Sent to the watch queue.' : 'This proposal has gone. Start again from the plan.'}</Text></Screen>;
  }
  return (
    <DiffView
      p={p}
      coach={flags.coach_enabled}
      brand={focused}
      onReview={() => router.push({ pathname: '/watch/confirm', params: { id } })}
      onBack={() => router.back()}
      onAsk={() => router.push({ pathname: '/coach', params: { about: p.source } })}
    />
  );
}
