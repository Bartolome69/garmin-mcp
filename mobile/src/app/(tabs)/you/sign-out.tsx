// Sign out, behind a confirm: the token and everything the phone keeps for the runner go
// (api/session.ts); the device's appearance stays. Then the welcome, as on a new phone.
// The root providers outlive the tabs, so what they hold in memory goes first: the watch
// queue (and any send under way, so nothing is written back), Plus and the flags.
import { useRouter } from 'expo-router';
import { useState } from 'react';

import { signOut } from '../../../api/session';
import { useDb } from '../../../db/DbContext';
import { useEntitlement } from '../../../features/plus/EntitlementsProvider';
import { SignOutSheet } from '../../../features/settings/SettingsViews';
import { useWatch } from '../../../features/watch/WatchProvider';
import { useFlags } from '../../../flags/FlagsProvider';

export default function YouSignOut() {
  const db = useDb();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const watch = useWatch();
  const entitlements = useEntitlement();
  const flags = useFlags();
  return (
    <SignOutSheet busy={busy} onKeep={() => router.back()} onSignOut={async () => {
      setBusy(true);
      watch.reset();
      entitlements.reset();
      flags.reset();
      await signOut(db).catch(() => {});
      if (router.canDismiss()) router.dismissAll();
      router.replace('/onboarding');
    }} />
  );
}
