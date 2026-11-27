import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { SeeSheet } from '../../../features/connections/ConnectionsView';

export default function See() {
  const { vm } = useConnections();
  return vm ? <SeeSheet vm={vm} /> : null;
}
