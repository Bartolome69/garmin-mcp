import { describe, expect, it, jest } from '@jest/globals';

let mockAnswer: () => Promise<{ granted: boolean }> = async () => ({ granted: true });
jest.mock('expo-notifications', () => ({ requestPermissionsAsync: () => mockAnswer() }));

import { askNotifications } from './notify';

describe('the notification ask', () => {
  it('says what the system answered', async () => {
    mockAnswer = async () => ({ granted: true });
    await expect(askNotifications()).resolves.toBe('granted');
    mockAnswer = async () => ({ granted: false });
    await expect(askNotifications()).resolves.toBe('denied');
  });
  it('a system that fails to ask counts as no, never a crash', async () => {
    mockAnswer = async () => { throw new Error('no native module'); };
    await expect(askNotifications()).resolves.toBe('denied');
  });
});
