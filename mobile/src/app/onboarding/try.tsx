// The builder before sign-up (build prompt, section 5.13): paces from a 10K of 45:00,
// everything else as in Plan's builder; sending needs an account.
import { useIsFocused, useRouter } from 'expo-router';

import { BuilderView } from '../../features/builder/BuilderView';
import { useBuilder } from '../../features/builder/useBuilder';
import { localToday } from '../../features/onboarding/model';
import { TryCard } from '../../features/onboarding/views';
import { useFlags } from '../../flags/FlagsProvider';

export const TRY_EXAMPLE = '5x1km at threshold, 2 min easy';

export default function TryScreen() {
  const router = useRouter();
  const focused = useIsFocused();
  const { flags } = useFlags();
  const b = useBuilder({ vm: null, today: localToday(), initial: TRY_EXAMPLE });
  return (
    <BuilderView {...b.view} coach={flags.coach_enabled} coachNote={null} onSave={() => {}} brand={focused}
      hint="Type a session. Paces use a 10K of 45:00 until you connect a watch."
      trying={<TryCard brand={focused} onCreate={() => router.push('/onboarding/goal')} />} />
  );
}
