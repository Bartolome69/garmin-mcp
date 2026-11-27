// Other options for today: as planned goes back; Swap a day and Rest and recover
// ask for the change, which opens as the new week, then the watch confirm.
import { useRouter } from 'expo-router';
import { Alert } from 'react-native';

import { OptionsSheet } from '../../../features/today/Sheets';
import { useToday } from '../../../features/today/TodayProvider';
import { useWatch } from '../../../features/watch/WatchProvider';

export default function OtherOptions() {
  const { data } = useToday();
  const watch = useWatch();
  const router = useRouter();
  if (!data) return null;
  return (
    <OptionsSheet
      options={data.options}
      onCancel={() => router.back()}
      onChoose={async (o) => {
        if (o.kind === 'as_planned') { router.back(); return; }
        try {
          const p = await watch.propose({ kind: o.kind });
          router.back();
          router.push({ pathname: '/today/new-week', params: { id: p.id } });
        } catch (err) {
          Alert.alert('Couldn’t work out the change', err instanceof Error ? err.message : 'Try again in a moment.');
        }
      }}
    />
  );
}
