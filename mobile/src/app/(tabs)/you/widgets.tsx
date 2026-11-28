import { useRouter } from 'expo-router';

import { usePlus } from '../../../features/plus/EntitlementsProvider';
import { openPaywall } from '../../../features/plus/open';
import { WidgetsView } from '../../../features/widgets/WidgetsView';
import { usePreviewSnapshot } from '../../../features/widgets/WidgetSync';

// Widgets setup: Plus. Free sees the previews behind a lock that opens Plus.
export default function WidgetsScreen() {
  const plus = usePlus();
  const router = useRouter();
  const snapshot = usePreviewSnapshot();
  return <WidgetsView snapshot={snapshot} plus={plus} onPlus={() => openPaywall(router, 'widgets')} />;
}
