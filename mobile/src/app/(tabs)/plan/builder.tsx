// The workout builder, from Plan's "New workout" (the iOS button, the Android FAB)
// and from a session's Edit (prefilled; saving replaces it). Cancel on the left on
// iOS, the back arrow on Android, and no top-right action (build prompt, section 5.7).
// Save and send stages the change and opens the watch confirm; once it's confirmed
// the builder closes.
import { Stack, useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Platform, Pressable } from 'react-native';

import { BuilderView } from '../../../features/builder/BuilderView';
import { buildProposal } from '../../../features/builder/model';
import { devCoachNote, useBuilder } from '../../../features/builder/useBuilder';
import { findSession } from '../../../features/plan/model';
import { usePlan } from '../../../features/plan/PlanProvider';
import { useWatch } from '../../../features/watch/WatchProvider';
import { useFlags } from '../../../flags/FlagsProvider';
import { MIN_TOUCH } from '../../../ui/Button';
import { Text } from '../../../ui/Text';

const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

export default function BuilderScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const plan = usePlan();
  const watch = useWatch();
  const { flags } = useFlags();
  const router = useRouter();
  const focused = useIsFocused();
  const vm = plan.shown;
  const replacing = id && vm ? findSession(vm, id) : null;
  const b = useBuilder({ vm, today: vm?.today ?? new Date().toISOString().slice(0, 10), replacing });
  const [submitted, setSubmitted] = useState<string | null>(null);
  const closed = useRef(false);

  // Confirmed: the change is in the watch queue, so the builder's job is done.
  useEffect(() => {
    if (!submitted || closed.current) return;
    if (watch.ops.some((o) => o.batch.startsWith(`${submitted}@`))) { closed.current = true; router.back(); }
  }, [submitted, watch.ops, router]);

  const save = () => {
    if (!b.built) return;
    const p = buildProposal(b.built, { id: `built-${Date.now().toString(36)}`, date: b.day, replacing });
    watch.stage(p);
    setSubmitted(p.id);
    router.push({ pathname: '/watch/confirm', params: { id: p.id } });
  };

  return (
    <>
      <Stack.Screen options={{
        title: replacing ? 'Edit workout' : 'New workout',
        ...(Platform.OS === 'ios' ? {
          headerBackVisible: false,
          headerLeft: () => (
            <Pressable accessibilityRole="button" onPress={() => router.back()}
              style={{ minHeight: MIN_TOUCH, minWidth: MIN_TOUCH, justifyContent: 'center' }}>
              <Text variant="body">Cancel</Text>
            </Pressable>
          ),
        } : {}),
      }} />
      <BuilderView
        {...b.view}
        coach={flags.coach_enabled}
        coachNote={flags.coach_enabled && USE_FIXTURE ? devCoachNote(vm, b.built, b.day) : null}
        onSave={save}
        brand={focused}
      />
    </>
  );
}
