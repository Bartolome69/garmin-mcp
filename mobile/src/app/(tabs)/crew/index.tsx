// SKETCH: Sitemap (Crew: "This week · nudge · invite") — the crew is the existing web feature
// (garmin_mcp/crews.py, the /crew/{link} page) and out of scope to redesign (build prompt,
// section 14). The app has no crew endpoint yet (TODO(server): GET /app/api/v1/crew with this
// week's easy and long runs, the crew's link to invite, and a nudge), so this shows the three
// parts and where the crew lives today. Needs: the endpoint, then the This week list, Invite
// (share the link) and Nudge.
import { Card } from '../../../ui/Card';
import { ListGroup, ListRow, SectionTitle } from '../../../ui/List';
import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

export default function CrewScreen() {
  return (
    <Screen testID="crew">
      <Card testID="crew-empty">
        <Text variant="headline" accessibilityRole="header">Your crew</Text>
        <Text variant="body" tone="muted">Friends in a crew see each other’s easy and long runs for the week. Rest days count.</Text>
        <Text variant="footnote" tone="muted">Crews are set up in Daash on the web for now. Your crew’s week shows here once the app can read it.</Text>
      </Card>
      <SectionTitle>In a crew</SectionTitle>
      <ListGroup>
        <ListRow title="This week" sub="Who’s running, and when" />
        <ListRow title="Nudge" sub="A friendly nudge for a friend’s next run" />
        <ListRow title="Invite" sub="Share the crew’s link. Friends without Garmin add their easy runs by hand." />
      </ListGroup>
    </Screen>
  );
}
