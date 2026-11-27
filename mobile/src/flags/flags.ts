// Feature flags and config (build prompt, section 10). Server-driven per person,
// so a change takes effect without an app update; cached on the device so the app
// opens with the last answer, offline included. Pure: no React, no network here.

export type ReadinessConfig = {
  /** Sleep counts as low when last night is this many hours under the 28-day median. */
  sleepShortHours: number;
  sleepMedianNights: number;
  /** HRV counts as low below this percentile of the 60-day personal range. */
  hrvRangeNights: number;
  hrvLowPercentile: number;
  hrvHighPercentile: number;
  /** 7-day / 28-day load ratio: in range between these. */
  loadRatioLow: number;
  loadRatioHigh: number;
};

export type MergeConfig = { startWithinSeconds: number; distanceWithinPct: number };

export type Flags = {
  coach_enabled: boolean;
  plus: boolean;
  coach_daily_limit: number;
  providers: Record<string, boolean>;
  readiness: ReadinessConfig;
  merge: MergeConfig;
};

// TODO(sports-science): readiness thresholds are the build prompt's placeholders
// (section 6), pending sign-off. They live here and on the server, never in code paths.
export const DEFAULT_FLAGS: Flags = {
  coach_enabled: false,
  plus: false,
  coach_daily_limit: 10,
  providers: { garmin: true },
  readiness: {
    sleepShortHours: 1,
    sleepMedianNights: 28,
    hrvRangeNights: 60,
    hrvLowPercentile: 10,
    hrvHighPercentile: 90,
    loadRatioLow: 0.8,
    loadRatioHigh: 1.3,
  },
  merge: { startWithinSeconds: 60, distanceWithinPct: 2 },
};

const num = (v: unknown, fallback: number) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

/**
 * The server's answer, read leniently: unknown keys are ignored, missing or
 * malformed ones keep the default. Today's server (#22) answers {"chat": true}
 * for the pilot; the v1 endpoint answers {"coach": true, ...}. Both mean coach_enabled.
 */
export function parseFlags(raw: unknown, base: Flags = DEFAULT_FLAGS): Flags {
  if (!raw || typeof raw !== 'object') return base;
  const r = raw as Record<string, unknown>;
  const coach = r.coach_enabled ?? r.coach ?? r.chat;
  const readiness = (r.readiness && typeof r.readiness === 'object' ? r.readiness : {}) as Record<string, unknown>;
  const merge = (r.merge && typeof r.merge === 'object' ? r.merge : {}) as Record<string, unknown>;
  const providers = r.providers && typeof r.providers === 'object'
    ? Object.fromEntries(Object.entries(r.providers as Record<string, unknown>).map(([k, v]) => [k, v === true]))
    : base.providers;
  return {
    coach_enabled: typeof coach === 'boolean' ? coach : base.coach_enabled,
    plus: typeof r.plus === 'boolean' ? r.plus : base.plus,
    coach_daily_limit: num(r.coach_daily_limit, base.coach_daily_limit),
    providers,
    readiness: Object.fromEntries(Object.entries(base.readiness).map(([k, v]) => [k, num(readiness[k], v)])) as ReadinessConfig,
    merge: { startWithinSeconds: num(merge.startWithinSeconds, base.merge.startWithinSeconds),
             distanceWithinPct: num(merge.distanceWithinPct, base.merge.distanceWithinPct) },
  };
}

export type TabName = 'today' | 'plan' | 'coach' | 'crew' | 'you';

/** Today · Plan · (Coach) · Crew · You. The Coach tab exists only with coach_enabled. */
export function tabsFor(flags: Pick<Flags, 'coach_enabled'>): TabName[] {
  return flags.coach_enabled ? ['today', 'plan', 'coach', 'crew', 'you'] : ['today', 'plan', 'crew', 'you'];
}
