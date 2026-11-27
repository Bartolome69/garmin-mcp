// "Changed your mind? · Remove from watch", once, after a send went through. It
// leads to the remove confirm for what that send put on the watch.
import { useRouter } from 'expo-router';
import { useEffect } from 'react';

import { unwatchProposal } from './proposals';
import { SendToast } from './Sheets';
import { useWatch } from './WatchProvider';

export const TOAST_MS = 8000;

export function WatchToast() {
  const watch = useWatch();
  const router = useRouter();
  const toast = watch.toast;
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(watch.dismissToast, TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!toast) return null;
  const put = watch.ops.filter((o) => o.batch === toast.batch && o.kind !== 'remove' && o.after).map((o) => o.after!);
  return (
    <SendToast
      onDismiss={watch.dismissToast}
      onRemove={() => {
        watch.dismissToast();
        const p = unwatchProposal(put, toast.batch);
        watch.stage(p);
        router.push({ pathname: '/watch/confirm', params: { id: p.id } });
      }}
    />
  );
}
