// Step 1, the goal. Opened from Plan's "Create a plan" (?from=plan) it starts the
// steps there, signed in already, with Cancel back to Plan.
import { Stack, useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';
import { Pressable } from 'react-native';

import { localToday } from '../../features/onboarding/model';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { GoalStep } from '../../features/onboarding/views';
import { MIN_TOUCH } from '../../ui/Button';
import { Text } from '../../ui/Text';

export default function GoalScreen() {
  const ob = useOnboarding();
  const router = useRouter();
  const focused = useIsFocused();
  const { from } = useLocalSearchParams<{ from?: string }>();
  const fromPlan = from === 'plan';
  const { setOrigin } = ob;
  useEffect(() => { if (fromPlan) setOrigin('plan'); }, [fromPlan, setOrigin]);
  return (
    <>
      {fromPlan ? (
        <Stack.Screen options={{
          headerLeft: () => (
            <Pressable testID="ob-cancel" accessibilityRole="button" onPress={() => router.back()}
              style={{ minHeight: MIN_TOUCH, minWidth: MIN_TOUCH, justifyContent: 'center' }}>
              <Text variant="body">Cancel</Text>
            </Pressable>
          ),
        }} />
      ) : null}
      <GoalStep initial={ob.goal} today={localToday()} brand={focused} onContinue={(g) => { ob.setGoal(g); router.push('/onboarding/paces'); }} />
    </>
  );
}
