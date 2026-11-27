// A recorded run, from a done row on Plan.
import { useLocalSearchParams } from 'expo-router';

import { RunScreen } from '../../../../features/run/RunScreen';

export default function PlanRun() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <RunScreen id={id} />;
}
