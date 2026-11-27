import { useIsFocused, useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking } from 'react-native';

import { API_BASE, NotSignedIn } from '../../api/client';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { importRuns } from '../../features/onboarding/source';
import { PacesStep } from '../../features/onboarding/views';

export default function PacesScreen() {
  const ob = useOnboarding();
  const router = useRouter();
  const focused = useIsFocused();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const read = async () => {
    setBusy(true); setError(null);
    try {
      ob.setResult(await importRuns());
      router.push('/onboarding/result');
    } catch (err) {
      // Not connected yet: Garmin's own sign-in, on the server. TODO(server): back to the app with a token.
      if (err instanceof NotSignedIn) Linking.openURL(`${API_BASE}/connect?app=native`).catch(() => {});
      else setError('Couldn’t read your runs just now. Try again, or answer 4 questions instead.');
    } finally {
      setBusy(false);
    }
  };
  return <PacesStep brand={focused} busy={busy} error={error} onImport={read} onQuestions={() => router.push('/onboarding/questions')} />;
}
