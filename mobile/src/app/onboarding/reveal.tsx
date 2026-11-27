// The plan reveal, then "Send week 1 to my watch": through the watch confirm like
// every other write, and on to the first workout landing.
import { useIsFocused, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { View } from 'react-native';

import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { makePlan } from '../../features/onboarding/source';
import { RevealView } from '../../features/onboarding/views';
import { weekOneProposal } from '../../features/onboarding/week1';
import { useWatch } from '../../features/watch/WatchProvider';
import { useFlags } from '../../flags/FlagsProvider';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { Text } from '../../ui/Text';

export default function RevealScreen() {
  const ob = useOnboarding();
  const watch = useWatch();
  const { flags } = useFlags();
  const router = useRouter();
  const focused = useIsFocused();
  const [error, setError] = useState<string | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const moved = useRef(false);

  const load = () => {
    if (!ob.result || !ob.goal) return;
    setError(null);
    makePlan(ob.result, ob.goal, flags.coach_enabled).then(ob.setReveal).catch(() => setError('Couldn’t make the plan just now.'));
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Confirmed: on to the landing.
  useEffect(() => {
    if (!staged || moved.current) return;
    if (watch.ops.some((o) => o.batch.startsWith(`${staged}@`))) { moved.current = true; router.replace('/onboarding/landed'); }
  }, [staged, watch.ops, router]);

  if (!ob.reveal) {
    return (
      <Screen testID="ob-reveal-wait">
        <View style={{ gap: 12 }}>
          <Text variant="body" tone="muted">{error ?? 'Making your plan…'}</Text>
          {error ? <Button label="Try again" onPress={load} /> : null}
        </View>
      </Screen>
    );
  }
  return (
    <RevealView r={ob.reveal} coach={flags.coach_enabled} brand={focused} busy={false} onSend={() => {
      const p = weekOneProposal(ob.reveal!);
      watch.stage(p);
      setStaged(p.id);
      router.push({ pathname: '/watch/confirm', params: { id: p.id } });
    }} />
  );
}
