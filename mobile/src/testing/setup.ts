// Every test starts on a phone at 100% text: 390 × 844 pt, as the boards are drawn.
// (React Native's Jest preset reports a 750 × 1334 window at 200% text.) The snapshot
// matrix and the large-layout tests set other sizes with setWindow (testing/screens.tsx).
// Times are formatted in UTC, so a snapshot reads the same on any machine.
import { Dimensions } from 'react-native';

process.env.TZ = 'UTC';
const window = { width: 390, height: 844, scale: 3, fontScale: 1 };
Dimensions.set({ window, screen: window });
