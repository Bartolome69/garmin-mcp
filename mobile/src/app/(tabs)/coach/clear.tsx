import { useRouter } from 'expo-router';
import { useState } from 'react';

import { useCoach } from '../../../features/coach/CoachProvider';
import { ClearSheet } from '../../../features/coach/Sheets';

// Clear conversation: destructive, so it asks first. Memory and the plan stay. The phone's
// copy goes only once the server's has; otherwise the thread stays and the sheet says so.
export default function CoachClear() {
  const coach = useCoach();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clear = async () => {
    setBusy(true);
    setError(null);
    const ok = await coach.clear();
    setBusy(false);
    if (ok) router.back();
    else setError('Couldn’t clear the conversation. It’s still here. Try again in a moment.');
  };
  return <ClearSheet onClear={() => { clear(); }} onKeep={() => router.back()} busy={busy} error={error}
    offline={coach.failure === 'offline'} />;
}
