// Session types are always colour + label + left bar (build prompt, section 2);
// colour is never the only cue.
import type { colors } from '../../theme/tokens';
import type { SessionType } from '../../features/today/model';

type Token = keyof (typeof colors)['dark'];

export const TYPE_TOKEN: Record<SessionType, Token> = {
  easy: 'cEasy', recovery: 'cEasy', tempo: 'cTempo', intervals: 'cInt', long: 'cLong',
  race: 'race', strength: 'cStr', rest: 'cRest', other: 'muted',
};
