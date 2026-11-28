import { describe, expect, it, jest } from '@jest/globals';

let mockAnswer: () => Promise<{ granted: boolean }> = async () => ({ granted: true });
const mockCalls: string[] = [];
jest.mock('expo-notifications', () => ({
  AndroidImportance: { DEFAULT: 3 },
  setNotificationChannelAsync: async (id: string) => { mockCalls.push(`channel:${id}`); return null; },
  requestPermissionsAsync: () => { mockCalls.push('ask'); return mockAnswer(); },
}));

import { askNotifications } from './notify';

describe('the notification ask', () => {
  it('says what the system answered', async () => {
    mockAnswer = async () => ({ granted: true });
    await expect(askNotifications('ios')).resolves.toBe('granted');
    mockAnswer = async () => ({ granted: false });
    await expect(askNotifications('ios')).resolves.toBe('denied');
  });
  it('a system that fails to ask counts as no, never a crash', async () => {
    mockAnswer = async () => { throw new Error('no native module'); };
    await expect(askNotifications('ios')).resolves.toBe('denied');
  });
  it('on Android the default channel exists before the ask (Android 13+ shows no prompt without one)', async () => {
    mockAnswer = async () => ({ granted: true });
    mockCalls.length = 0;
    await askNotifications('android');
    expect(mockCalls).toEqual(['channel:default', 'ask']);
    mockCalls.length = 0;
    await askNotifications('ios');
    expect(mockCalls).toEqual(['ask']);
  });
});
