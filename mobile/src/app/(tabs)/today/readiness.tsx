import { useLocalSearchParams, useRouter } from 'expo-router';

import { ReadinessSheet } from '../../../features/today/Sheets';
import { useToday } from '../../../features/today/TodayProvider';
import { useFlags } from '../../../flags/FlagsProvider';

export default function ReadinessReasons() {
  const { signal } = useLocalSearchParams<{ signal?: string }>();
  const { data } = useToday();
  const router = useRouter();
  const { flags } = useFlags();
  const s = data?.readiness.signals.find((x) => x.key === signal) ?? data?.readiness.signals[0];
  if (!s) return null;
  return <ReadinessSheet signal={s} coach={flags.coach_enabled} onDone={() => router.back()} />;
}
