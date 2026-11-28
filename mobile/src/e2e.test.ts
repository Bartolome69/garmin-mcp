// The build prompt's five end-to-end flows (section 13): each has a flow test over the real
// routes and watch queue here, and a Maestro flow in mobile/e2e/ for a device run (needs a
// development build; see docs/native-app/WALKTHROUGHS.md).
import { describe, expect, it } from '@jest/globals';
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..');
const read = (p: string) => fs.readFileSync(path.join(ROOT, p), 'utf8');

const FLOWS: { flow: string; test: [string, RegExp]; maestro: string }[] = [
  { flow: 'onboarding → first workout lands', test: ['src/onboarding.flows.test.tsx', /2\. Onboarding → first workout lands/], maestro: 'e2e/5-onboarding.yaml' },
  { flow: 'Today "Make it easier" → confirm → on watch', test: ['src/flows.test.tsx', /1\. Today "Make it easier" → confirm → on your watch/], maestro: 'e2e/1-easier-on-watch.yaml' },
  { flow: 'Life happened → diff → confirm', test: ['src/flows.test.tsx', /2\. Life happened → new week → confirm/], maestro: 'e2e/2-life-happened.yaml' },
  { flow: 'send fail → resend', test: ['src/flows.test.tsx', /3\. Send fails → Resend/], maestro: 'e2e/3-send-fail-resend.yaml' },
  { flow: 'remove from watch', test: ['src/flows.test.tsx', /4\. Remove from watch/], maestro: 'e2e/4-remove-from-watch.yaml' },
];

describe('the five end-to-end flows', () => {
  it.each(FLOWS.map((f) => [f.flow, f] as const))('%s: a flow test and a Maestro flow', (_name, f) => {
    expect(read(f.test[0])).toMatch(f.test[1]);
    expect(fs.existsSync(path.join(ROOT, f.maestro))).toBe(true);
  });
});
