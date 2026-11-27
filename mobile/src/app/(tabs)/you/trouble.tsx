import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { TroubleSheet } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';

export default function Trouble() {
  const { vm } = useConnections();
  return vm ? <TroubleSheet vm={vm} onReconnect={reconnect} /> : null;
}
