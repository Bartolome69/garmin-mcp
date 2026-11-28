// A recorded run, from Activities.
import { useLocalSearchParams } from 'expo-router';

import { RunScreen } from '../../../../features/run/RunScreen';

export default function YouRun() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <RunScreen id={id} />;
}
