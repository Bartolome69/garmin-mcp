import { Stack, useIsFocused, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, Platform, useWindowDimensions, View } from 'react-native';

import { freshness } from '../../../data/freshness';
import { useToday } from '../../../features/today/TodayProvider';
import { TodayEmpty, TodayView } from '../../../features/today/TodayView';
import { applyToToday } from '../../../features/watch/apply';
import { useWatch } from '../../../features/watch/WatchProvider';
import { useFlags } from '../../../flags/FlagsProvider';
import { useTheme } from '../../../theme/ThemeProvider';
import { Text } from '../../../ui/Text';

function useNow(everyMs = 60000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), everyMs); return () => clearInterval(t); }, [everyMs]);
  return now;
}

export default function TodayScreen() {
  const today = useToday();
  const watch = useWatch();
  const { fetchedAt, refreshing, error, refresh } = today;
  // Confirmed changes and the watch queue's state, over the server's last answer.
  const data = today.data ? applyToToday(today.data, watch.applied, watch.ops) : null;
  const { flags } = useFlags();
  const { colors } = useTheme();
  const router = useRouter();
  // A screen behind an open sheet loses its brand fill (build prompt, section 2).
  const focused = useIsFocused();
  const { fontScale } = useWindowDimensions();
  const fresh = freshness(fetchedAt, useNow());

  if (!data) return <TodayEmpty error={error} onRetry={refresh} />;
  return (
    <>
      {Platform.OS === 'android' && fresh?.stale ? (
        <Stack.Screen options={{ headerRight: () => (
          <View accessible accessibilityLabel={fresh.label} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingRight: 4 }}>
            <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: colors.text2 }} />
            <Text variant="footnote" tone="muted">{fresh.label}</Text>
          </View>
        ) }} />
      ) : null}
      <TodayView
        vm={data}
        coach={flags.coach_enabled}
        brand={focused}
        fontScale={fontScale}
        fresh={fresh}
        refreshing={refreshing}
        error={error}
        actions={{
          signal: (key) => router.push({ pathname: '/today/readiness', params: { signal: key } }),
          how: () => router.push({ pathname: '/today/readiness', params: { signal: data.readiness.signals.find((s) => s.status === 'low')?.key ?? 'sleep' } }),
          recommended: async () => {
            if (data.readiness.recommended.kind !== 'easier') {
              if (data.session) router.push({ pathname: '/plan/session/[id]', params: { id: data.session.id } });
              return;
            }
            try {
              const p = await watch.propose({ kind: 'easier' });
              router.push({ pathname: '/today/new-week', params: { id: p.id } });
            } catch (err) {
              Alert.alert('Couldn’t work out an easier day', err instanceof Error ? err.message : 'Try again in a moment.');
            }
          },
          options: () => router.push('/today/options'),
          askWhy: () => router.push({ pathname: '/coach', params: { about: 'readiness', date: data.date } }),
          openSession: () => data.session && router.push({ pathname: '/plan/session/[id]', params: { id: data.session.id } }),
          openRun: (id) => router.push({ pathname: '/today/run/[id]', params: { id } }),
          refresh,
        }}
      />
    </>
  );
}
