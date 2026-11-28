// How the server answers Today's and Plan's sources in a state case of the snapshot matrix
// (src/matrix/states.test.tsx mocks the sources to read this; openScreen sets it). Elsewhere
// nothing reads it: the sources answer from the fixtures as usual. Test-only.
import { NotSignedIn, Offline, ReconnectNeeded } from '../api/client';

export type DataMode = 'ok' | 'loading' | 'offline' | 'reconnect' | 'signedOut' | 'empty';
export const dataMode: { current: DataMode } = { current: 'ok' };

/** A source as the server would answer it in the current mode; `empty` makes the empty answer. */
export function answering<T>(ok: () => Promise<T>, empty: (v: T) => T): () => Promise<T> {
  return async () => {
    switch (dataMode.current) {
      case 'loading': return new Promise<T>(() => {});
      case 'offline': throw new Offline();
      case 'reconnect': throw new ReconnectNeeded('Garmin');
      case 'signedOut': throw new NotSignedIn();
      case 'empty': return empty(await ok());
      default: return ok();
    }
  };
}
