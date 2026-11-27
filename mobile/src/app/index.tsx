// The first screen: onboarding until it's done on this device, then Today.
import { Redirect } from 'expo-router';
import { useEffect, useState } from 'react';

import { useDb } from '../db/DbContext';
import { kv } from '../db/kv';
import { ONBOARDED_KEY } from '../features/onboarding/OnboardingProvider';

export default function Index() {
  const db = useDb();
  const [onboarded, setOnboarded] = useState<boolean | null>(null);
  useEffect(() => {
    if (!db) { setOnboarded(false); return; }
    kv(db).get<boolean>(ONBOARDED_KEY).then((v) => setOnboarded(!!v)).catch(() => setOnboarded(false));
  }, [db]);
  if (onboarded == null) return null;
  return <Redirect href={onboarded ? '/today' : '/onboarding'} />;
}
