// The watch confirm, over whichever tab asked for it: a staged proposal's exact
// watch operations, and "Confirm and send". The only way a change reaches the queue.
import { useLocalSearchParams, useRouter } from 'expo-router';

import { ConfirmSheet } from '../../features/watch/Sheets';
import { useWatch } from '../../features/watch/WatchProvider';

export default function WatchConfirm() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const watch = useWatch();
  const router = useRouter();
  const p = watch.draft(id);
  if (!p) return null;
  return (
    <ConfirmSheet p={p} connection={watch.connection}
      onConfirm={() => { watch.confirm(id); router.back(); }}
      onBack={() => router.back()} />
  );
}
