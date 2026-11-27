import { useIsFocused, useRouter } from 'expo-router';
import { Linking } from 'react-native';

import { API_BASE } from '../../api/client';
import { Welcome } from '../../features/onboarding/views';

// Sign in is Garmin's own sign-in on the server: connecting is the account.
// TODO(server): hand the app its token back (a daash:// link after /connect).
export default function WelcomeScreen() {
  const router = useRouter();
  const focused = useIsFocused();
  return (
    <Welcome brand={focused} onTry={() => router.push('/onboarding/try')} onSetUp={() => router.push('/onboarding/goal')}
      onSignIn={() => { Linking.openURL(`${API_BASE}/connect?app=native`).catch(() => {}); }} />
  );
}
