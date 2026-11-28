// SKETCH: Sitemap ("Activities is reached from Today, Plan and You") — no board for the list
// itself and no endpoint yet (TODO(server): GET /app/api/v1/runs). Development builds and sample
// data list the persona's runs; each opens its post-run screen in You's stack.
import { useRouter } from 'expo-router';
import { useMemo } from 'react';

import { useResource } from '../../../data/resource';
import { runListSource } from '../../../features/run/source';
import { ActivitiesView } from '../../../features/settings/SettingsViews';
import { LoadState } from '../../../ui/StateViews';

export default function YouActivities() {
  const router = useRouter();
  const fetcher = useMemo(() => runListSource(), []);
  const r = useResource(fetcher);
  if (!r.data) return <LoadState what="your runs" failure={r.failure} error={r.error} onRetry={r.refresh} testID="activities" />;
  return <ActivitiesView runs={r.data} onOpen={(id) => router.push({ pathname: '/you/run/[id]', params: { id } })} />;
}
