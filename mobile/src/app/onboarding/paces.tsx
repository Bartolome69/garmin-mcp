// Step 2, paces: import 12 weeks of runs (recommended), or answer 4 questions.
// Not connected yet, importing starts at Garmin's own sign-in.
import { useIsFocused, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';

import { accessToken } from '../../api/client';
import { failureKind, type FailureKind } from '../../data/resource';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { importRuns } from '../../features/onboarding/source';
import { PacesStep } from '../../features/onboarding/views';
import { openGarminSignIn } from '../../ui/StateViews';

export default function PacesScreen() {
  const ob = useOnboarding();
  const router = useRouter();
  const focused = useIsFocused();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<FailureKind | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => { accessToken().then((t) => setConnected(!!t)).catch(() => {}); }, []);
  const read = async () => {
    setBusy(true); setProblem(null);
    try {
      ob.setResult(await importRuns());
      router.push('/onboarding/result');
    } catch (err) {
      const kind = failureKind(err);
      // Not connected, or Garmin's login expired: Garmin's own sign-in, on the server.
      // TODO(server): back to the app with a token (a daash:// link after /connect).
      if (kind === 'signedOut' || kind === 'reconnect') openGarminSignIn();
      setProblem(kind);
    } finally {
      setBusy(false);
    }
  };
  return <PacesStep brand={focused} busy={busy} connected={connected} problem={problem} onImport={read} onQuestions={() => router.push('/onboarding/questions')} />;
}
