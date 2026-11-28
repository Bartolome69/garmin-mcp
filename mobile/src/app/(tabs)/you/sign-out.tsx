// Sign out, behind a confirm: the token and everything the phone keeps for the runner go
// (api/session.ts); the device's appearance stays. Then the welcome, as on a new phone.
import { useRouter } from 'expo-router';
import { useState } from 'react';

import { signOut } from '../../../api/session';
import { useDb } from '../../../db/DbContext';
import { SignOutSheet } from '../../../features/settings/SettingsViews';

export default function YouSignOut() {
  const db = useDb();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <SignOutSheet busy={busy} onKeep={() => router.back()} onSignOut={async () => {
      setBusy(true);
      await signOut(db).catch(() => {});
      if (router.canDismiss()) router.dismissAll();
      router.replace('/onboarding');
    }} />
  );
}
