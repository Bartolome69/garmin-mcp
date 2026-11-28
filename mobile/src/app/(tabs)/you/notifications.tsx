// SKETCH: Notifications-ios / Notifications-android — the switches are kept on this phone; the
// server's pushes that would read them (session heads-up, sent to your watch, run summary, plan
// updated) don't exist yet (TODO(server)), and the copy (features/settings/notifications.ts)
// needs a device build to see as real notifications.
import { useEffect, useState } from 'react';

import { useDb } from '../../../db/DbContext';
import { kv } from '../../../db/kv';
import { DEFAULT_PREFS, PREFS_KEY, readPrefs, type NotificationPrefs } from '../../../features/settings/notifications';
import { NotificationsView } from '../../../features/settings/SettingsViews';

export default function YouNotifications() {
  const db = useDb();
  const [prefs, setPrefs] = useState<NotificationPrefs>(DEFAULT_PREFS);
  useEffect(() => {
    if (!db) return;
    kv(db).get(PREFS_KEY).then((raw) => setPrefs(readPrefs(raw))).catch(() => {});
  }, [db]);
  return (
    <NotificationsView prefs={prefs} allowed={null} onChange={(kind, on) => {
      const next = { ...prefs, [kind]: on };
      setPrefs(next);
      if (db) kv(db).set(PREFS_KEY, next).catch(() => {});
    }} />
  );
}
