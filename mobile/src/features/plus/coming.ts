// Two Plus features the prompt lists have no engine yet (DECISIONS, milestone 10:
// an owner decision): advanced plan styles and advanced merge rules. They stay
// visible and locked, and every place that names them says they're coming, so
// nobody buys Plus for something that isn't there yet.

export const COMING_NOTE = 'Coming in an update';

/** What a Plus runner sees on opening one of them. */
export const COMING: Record<string, string> = {
  plan_styles: 'Advanced plan styles come with your Plus and arrive in an update.',
  merge_rules: 'Advanced merge rules come with your Plus and arrive in an update.',
};
