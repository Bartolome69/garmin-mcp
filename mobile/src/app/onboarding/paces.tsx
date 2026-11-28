// Step 2, paces: import 12 weeks of runs (recommended), or answer 4 questions.
// Not connected yet, importing starts at Garmin's own sign-in.
import { useIsFocused, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';

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
  // Each import has a number; choosing the four questions, or leaving, makes a slow one stale.
  const request = useRef(0);
  useEffect(() => {
    accessToken().then((t) => setConnected(!!t)).catch(() => {});
    return () => { request.current += 1; };
  }, []);
  const read = async () => {
    const id = ++request.current;
    setBusy(true); setProblem(null);
    try {
      const r = await importRuns();
      if (id !== request.current) return;
      ob.setResult(r);
      router.push('/onboarding/result');
    } catch (err) {
      if (id !== request.current) return;
      const kind = failureKind(err);
      // Not connected, or Garmin's login expired: Garmin's own sign-in, on the server.
      // TODO(server): back to the app with a token (a daash:// link after /connect).
      if (kind === 'signedOut' || kind === 'reconnect') openGarminSignIn();
      setProblem(kind);
    } finally {
      if (id === request.current) setBusy(false);
    }
  };
  const questions = () => {
    request.current += 1;   // a late import must not push its result over the questions
    setBusy(false);
    router.push('/onboarding/questions');
  };
  return <PacesStep brand={focused} busy={busy} connected={connected} problem={problem} onImport={() => { void read(); }} onQuestions={questions} />;
}
