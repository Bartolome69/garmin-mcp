import { describe, expect, it } from '@jest/globals';

import { checkMove, clashes, isHard } from './backToBack';

describe('back-to-back while dragging', () => {
  const week = [
    { id: 't', date: '2026-11-24', type: 'tempo', distance_km: 10 },
    { id: 'e', date: '2026-11-26', type: 'easy', distance_km: 8 },
    { id: 'i', date: '2026-11-27', type: 'intervals', distance_km: 9 },
    { id: 'l', date: '2026-11-29', type: 'long', distance_km: 22 },
  ];
  it('says which two days, in words, before anything changes', () => {
    expect(checkMove(week, 't', '2026-11-26').map((c) => c.message)).toEqual(['Thu tempo and Fri intervals would be back to back']);
  });
  it('allows a move that leaves no two hard days together (a 22 km long run is not hard)', () => {
    expect(checkMove(week, 'l', '2026-11-28')).toEqual([]);
    expect(clashes(week)).toEqual([]);
  });
  it('counts a long run as hard only over 25 km', () => {
    expect(isHard({ id: 'a', date: '2026-11-29', type: 'long', distance_km: 25 })).toBe(false);
    expect(isHard({ id: 'b', date: '2026-11-29', type: 'long', distance_km: 25.5 })).toBe(true);
  });
  it('works across a month end and the clocks going back', () => {
    expect(clashes([{ id: 'a', date: '2026-10-31', type: 'tempo' }, { id: 'b', date: '2026-11-01', type: 'race' }])
      .map((c) => c.message)).toEqual(['Sat tempo and Sun race would be back to back']);
    expect(clashes([{ id: 'a', date: '2026-10-24', type: 'tempo' }, { id: 'b', date: '2026-10-25', type: 'intervals' }])).toHaveLength(1);
  });
});
