import { useRouter } from 'expo-router';

import { OptionsSheet } from '../../../features/today/Sheets';
import { useToday } from '../../../features/today/TodayProvider';

export default function OtherOptions() {
  const { data } = useToday();
  const router = useRouter();
  if (!data) return null;
  return (
    <OptionsSheet
      options={data.options}
      onCancel={() => router.back()}
      onChoose={(o) => (o.kind === 'as_planned' ? router.back() : router.replace({ pathname: '/today/next', params: { kind: o.kind } }))}
    />
  );
}
