import { Redirect } from 'expo-router';

import { useFlags } from '../../../flags/FlagsProvider';
import { Screen } from '../../../ui/Screen';

// Empty until milestone 11. Only reachable with coach_enabled; the tab is hidden otherwise.
export default function CoachScreen() {
  const { flags } = useFlags();
  if (!flags.coach_enabled) return <Redirect href="/today" />;
  return <Screen testID="coach" />;
}
