// The first screen: onboarding until it's done on this device, then Today. A phone
// that's signed in already (a token from before onboarding existed, or Sign in) goes
// to Today too: that runner has an account, and Plan offers "Create a plan" if there's
// no plan yet.
import { Redirect } from 'expo-router';
import { useEffect, useState } from 'react';

import { accessToken } from '../api/client';
import { sampleData } from '../data/sample';
import { useDb } from '../db/DbContext';
import { kv } from '../db/kv';
import { ONBOARDED_KEY } from '../features/onboarding/OnboardingProvider';

export default function Index() {
  const db = useDb();
  const [onboarded, setOnboarded] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    (async () => {
      const done = db ? await kv(db).get<boolean>(ONBOARDED_KEY).catch(() => null) : null;
      const token = done ? null : await accessToken().catch(() => null);
      if (live) setOnboarded(!!done || !!token);
    })();
    return () => { live = false; };
  }, [db]);
  // Sample data (Expo Go with nobody signed in): no sign-in wall; the persona is onboarded. You has "Try onboarding".
  if (sampleData()) return <Redirect href="/today" />;
  if (onboarded == null) return null;
  return <Redirect href={onboarded ? '/today' : '/onboarding'} />;
}
