// "Don't see your device? Request it": a name, sent when Daash can be reached.
// TODO(server): POST /app/api/v1/connections/request {device}.
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { TextInput, View } from 'react-native';

import { postJson } from '../../../api/client';
import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { useTheme } from '../../../theme/ThemeProvider';
import { Button } from '../../../ui/Button';
import { Text } from '../../../ui/Text';

export default function Request() {
  const { colors } = useTheme();
  const { ask } = useConnections();
  const router = useRouter();
  const [device, setDevice] = useState('');
  const [sent, setSent] = useState(false);
  const send = () => {
    const d = device.trim();
    if (!d) return;
    ask(`request:${d}`);
    postJson('/app/api/v1/connections/request', { device: d }).catch(() => {});
    setSent(true);
  };
  return (
    <View testID="request-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">Request a device</Text>
      {sent ? (
        <>
          <Text testID="request-sent" variant="body">Thanks. Every request is read, and the most asked-for come first.</Text>
          <Button label="Done" onPress={() => router.back()} />
        </>
      ) : (
        <>
          <Text variant="footnote" tone="muted">Which watch or app should Daash connect to?</Text>
          <TextInput testID="request-input" accessibilityLabel="Watch or app" value={device} onChangeText={setDevice}
            placeholder="Polar Vantage, Wahoo, Strava…" placeholderTextColor={colors.muted} autoFocus returnKeyType="send" onSubmitEditing={send}
            style={{ minHeight: 48, paddingHorizontal: 14, borderRadius: 12, backgroundColor: colors.sunken, color: colors.text, fontSize: 17 }} />
          <Button label="Send request" kind="primary" disabled={!device.trim()} onPress={send} />
        </>
      )}
    </View>
  );
}
