import { useIsFocused, useRouter } from 'expo-router';

import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { GoalStep } from '../../features/onboarding/views';

export default function GoalScreen() {
  const ob = useOnboarding();
  const router = useRouter();
  const focused = useIsFocused();
  return <GoalStep initial={ob.goal} brand={focused} onContinue={(g) => { ob.setGoal(g); router.push('/onboarding/paces'); }} />;
}
