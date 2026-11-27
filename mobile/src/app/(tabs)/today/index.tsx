import { Stack, useIsFocused, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Platform, useWindowDimensions, View } from 'react-native';

import { freshness } from '../../../data/freshness';
import { useToday } from '../../../features/today/TodayProvider';
import { TodayEmpty, TodayView } from '../../../features/today/TodayView';
import { useFlags } from '../../../flags/FlagsProvider';
import { useTheme } from '../../../theme/ThemeProvider';
import { Text } from '../../../ui/Text';

function useNow(everyMs = 60000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), everyMs); return () => clearInterval(t); }, [everyMs]);
  return now;
}

export default function TodayScreen() {
  const { data, fetchedAt, refreshing, error, refresh } = useToday();
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
          recommended: () => router.push({ pathname: '/today/next', params: { kind: data.readiness.recommended.kind } }),
          options: () => router.push('/today/options'),
          askWhy: () => router.push({ pathname: '/coach', params: { about: 'readiness', date: data.date } }),
          openSession: () => data.session && router.push({ pathname: '/plan/session/[id]', params: { id: data.session.id } }),
          refresh,
        }}
      />
    </>
  );
}
