import { useLocalSearchParams, useRouter } from 'expo-router';

import { ReadinessSheet } from '../../../features/today/Sheets';
import { useToday } from '../../../features/today/TodayProvider';

export default function ReadinessReasons() {
  const { signal } = useLocalSearchParams<{ signal?: string }>();
  const { data } = useToday();
  const router = useRouter();
  const s = data?.readiness.signals.find((x) => x.key === signal) ?? data?.readiness.signals[0];
  if (!s) return null;
  return <ReadinessSheet signal={s} onDone={() => router.back()} />;
}
