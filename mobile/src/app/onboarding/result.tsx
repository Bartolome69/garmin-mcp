// What the import (or the four answers) found, to confirm: Looks right, or Adjust
// with the four questions.
import { Redirect, useIsFocused, useRouter } from 'expo-router';

import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { ResultStep } from '../../features/onboarding/views';
import { useWatch } from '../../features/watch/WatchProvider';

export default function ResultScreen() {
  const ob = useOnboarding();
  const watch = useWatch();
  const router = useRouter();
  const focused = useIsFocused();
  if (!ob.result) return <Redirect href="/onboarding/paces" />;
  return (
    <ResultStep result={ob.result} device={watch.connection.device} brand={focused}
      onAdjust={() => router.push('/onboarding/questions')} onRight={() => router.push('/onboarding/reveal')} />
  );
}
