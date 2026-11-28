// Which Coach the app talks to. A store build: the server's (api.ts). A development
// build (and the layout check's web build) falls back to the persona's stand-in
// (fake.ts) when the server can't be reached or has no Coach for this account, as
// every other screen's data does.
import { createContext } from 'react';

import { serverCoachApi, type CoachApi } from './api';
import { devThread, FakeCoach } from './fake';

const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

/** Tests hand the provider their own Coach here. */
export const CoachApiContext = createContext<CoachApi | null>(null);

function withFallback(server: CoachApi, fake: CoachApi): CoachApi {
  const either = <T,>(a: () => Promise<T>, b: () => Promise<T>) => a().catch(() => b());
  return {
    thread: () => either(() => server.thread(), () => fake.thread()),
    async send(body, onEvent, signal) {
      let started = false;
      try {
        await server.send(body, (e) => { started = true; onEvent(e); }, signal);
      } catch (err) {
        if (started) throw err;
        await fake.send(body, onEvent, signal);
      }
    },
    decide: (id, d) => either(() => server.decide(id, d), () => fake.decide(id, d)),
    feedback: (id, v) => either(() => server.feedback(id, v), () => fake.feedback(id, v)),
    clear: () => either(() => server.clear(), () => fake.clear()),
  };
}

let shared: CoachApi | null = null;

export function defaultCoachApi(): CoachApi {
  if (!shared) shared = USE_FIXTURE ? withFallback(serverCoachApi(), new FakeCoach({ messages: devThread(Date.now()) })) : serverCoachApi();
  return shared;
}
