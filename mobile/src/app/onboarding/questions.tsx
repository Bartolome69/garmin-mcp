// The four questions, worked out on the phone with the shared pace engine.
import { useIsFocused, useRouter } from 'expo-router';

import { questions } from '../../features/onboarding/model';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { QuestionsStep } from '../../features/onboarding/views';

export default function QuestionsScreen() {
  const ob = useOnboarding();
  const router = useRouter();
  const focused = useIsFocused();
  return (
    <QuestionsStep brand={focused} onContinue={(a) => {
      ob.setResult(questions(a.experience, a.days, a.weekly, a.race));
      router.push('/onboarding/result');
    }} />
  );
}
