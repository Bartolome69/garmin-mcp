import { useIsFocused, useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking } from 'react-native';

import { accessToken, API_BASE } from '../../api/client';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { Welcome } from '../../features/onboarding/views';

// Sign in is Garmin's own sign-in on the server: connecting is the account. A runner
// who signs in has an account (and maybe a plan) already, so once the app has a
// token, "I've signed in" marks the phone onboarded and goes to Today; a runner with
// no plan yet finds "Create a plan" on Plan.
// TODO(server): hand the app its token back (a daash:// link after /connect).
export default function WelcomeScreen() {
  const router = useRouter();
  const focused = useIsFocused();
  const ob = useOnboarding();
  const [signingIn, setSigningIn] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const signedIn = async () => {
    const token = await accessToken().catch(() => null);
    if (!token) { setProblem('Not signed in yet. Sign in on Garmin’s page, then come back here.'); return; }
    setProblem(null);
    await ob.finish();
    router.replace('/today');
  };
  return (
    <Welcome brand={focused} onTry={() => router.push('/onboarding/try')} onSetUp={() => router.push('/onboarding/goal')}
      onSignIn={() => { setSigningIn(true); setProblem(null); Linking.openURL(`${API_BASE}/connect?app=native`).catch(() => {}); }}
      signingIn={signingIn} signInProblem={problem} onSignedIn={signedIn} />
  );
}
