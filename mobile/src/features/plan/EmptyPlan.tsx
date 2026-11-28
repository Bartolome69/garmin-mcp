// No plan yet (the board's "Empty plan"): what a plan is, and that nothing reaches
// the watch without a confirm. "Create a plan" belongs to onboarding (milestone 9),
// which passes onCreate; until then, sessions can still be planned one by one.
import { ScrollView, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Text } from '../../ui/Text';

export function EmptyPlan({ brand, onCreate, onWeekByWeek }: { brand: boolean; onCreate?: () => void; onWeekByWeek: () => void }) {
  const { colors } = useTheme();
  return (
    <ScrollView testID="plan-empty" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={{ padding: 16 }}>
      <Card style={{ alignItems: 'center', gap: 10, paddingVertical: 32, paddingHorizontal: 20 }}>
        <Text variant="title" accessibilityRole="header" style={{ textAlign: 'center' }}>No plan yet</Text>
        <Text variant="body" tone="muted" style={{ textAlign: 'center' }}>
          Pick a race or a goal and Daash builds the weeks. Nothing goes to your watch until you confirm it.
        </Text>
        <View style={{ alignSelf: 'stretch', gap: 8, marginTop: 6 }}>
          {onCreate ? <Button label="Create a plan" kind="primary" brand={brand} onPress={onCreate} /> : null}
          <Button label="Or plan week by week" kind={onCreate ? 'secondary' : 'primary'} brand={brand} onPress={onWeekByWeek} />
        </View>
      </Card>
    </ScrollView>
  );
}
