// Target paces from the best recent race: the twin of garmin_mcp/domain/paces.py,
// held to the same answers by garmin_mcp/domain/fixtures/paces.json. On the device
// for the builder before sign-up (a 10K of 45:00 until a watch is connected).

import { mmss, roundHalfUp, shortDate } from './format';

export const ZONES = ['easy', 'steady', 'marathon', 'threshold', 'tempo', '10k', '5k', 'vo2'] as const;
export type Zone = (typeof ZONES)[number];
export const ZONE_WORDS: Record<Zone, string> = {
  easy: 'Easy', steady: 'Steady', marathon: 'Marathon', threshold: 'Threshold', tempo: 'Tempo',
  '10k': '10K', '5k': '5K', vo2: 'VO2',
};

const RIEGEL = 1.06;
const STEP = 0.02;
const RACE_NAMES: Record<string, string> = { '5': '5K', '10': '10K', '21.0975': 'half marathon', '42.195': 'marathon' };

export const riegel = (seconds: number, fromKm: number, toKm: number) => seconds * (toKm / fromKm) ** RIEGEL;
export const hourDistance = (seconds: number, km: number) => km * (3600 / seconds) ** (1 / RIEGEL);

export type Paces = { zones: Record<Zone, [number, number]>; basis: string; step: number };

/** Seconds per km, fast to slow, for each zone. Harder (+n) is 2% faster a step, Easier (−n) 2% slower. */
export function paces(raceKm: number, raceSeconds: number, opts: { raceDate?: string | null; step?: number } = {}): Paces {
  const step = opts.step ?? 0;
  const at = (km: number) => riegel(raceSeconds, raceKm, km) / km;
  const marathon = at(42.195), half = at(21.0975), ten = at(10), five = at(5), three = at(3);
  const threshold = 3600 / hourDistance(raceSeconds, raceKm);
  const raw: Record<Zone, [number, number]> = {
    easy: [marathon * 1.15, marathon * 1.3],
    steady: [marathon * 1.05, marathon * 1.12],
    marathon: [marathon * 0.99, marathon * 1.01],
    threshold: [threshold * 0.99, threshold * 1.01],
    tempo: [half * 1.0, half * 1.03],
    '10k': [ten * 0.99, ten * 1.01],
    '5k': [five * 0.99, five * 1.01],
    vo2: [three, five],
  };
  const factor = step > 0 ? (1 - STEP) ** step : (1 + STEP) ** -step;
  const zones = Object.fromEntries(ZONES.map((z) => [z, [roundHalfUp(raw[z][0] * factor), roundHalfUp(raw[z][1] * factor)]])) as Paces['zones'];
  const key = String(Math.round(raceKm * 10000) / 10000);
  const name = RACE_NAMES[key] ?? `${raceKm} km`;
  const when = opts.raceDate ? ` (${shortDate(opts.raceDate)})` : '';
  return { zones, basis: `Based on your ${name} of ${mmss(raceSeconds)}${when}`, step };
}

export const defaultPaces = () => paces(10, 45 * 60);

export const paceRangeWords = ([fast, slow]: [number, number]) => `${mmss(fast)}–${mmss(slow)} /km`;
