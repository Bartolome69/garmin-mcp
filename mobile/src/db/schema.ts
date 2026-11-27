// The local store (build prompt, section 7): the source the UI reads. Server
// sync writes into it. Additive migrations only, numbered, each run once.
//
// Watch writes go through watch_operations and nothing else: every row starts
// at not_sent and must pass confirming before it can be sent.

export const WATCH_OPERATION_TYPES = ['add', 'replace', 'remove'] as const;
export const WATCH_STATES = ['not_sent', 'confirming', 'sending', 'on_watch', 'changed_pending_sync', 'failed'] as const;
export const STREAM_METRICS = ['pace', 'hr', 'cadence', 'elevation'] as const;

const list = (values: readonly string[]) => values.map((v) => `'${v}'`).join(', ');

export const MIGRATIONS: { version: number; sql: string }[] = [
  {
    version: 1,
    sql: `
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE sync_state (resource TEXT PRIMARY KEY, synced_at INTEGER NOT NULL, etag TEXT);

CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, initial TEXT, email_masked TEXT, updated_at INTEGER NOT NULL);
CREATE TABLE goals (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, race_name TEXT, race_date TEXT, distance_km REAL,
  target_seconds INTEGER, updated_at INTEGER NOT NULL);

CREATE TABLE plans (id TEXT PRIMARY KEY, label TEXT, name TEXT, starts TEXT, ends TEXT, updated_at INTEGER NOT NULL);
CREATE TABLE weeks (
  id TEXT PRIMARY KEY, plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE, starts TEXT NOT NULL,
  number INTEGER, phase TEXT, planned_load REAL, updated_at INTEGER NOT NULL);
CREATE INDEX weeks_starts ON weeks (starts);
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE, week_id TEXT REFERENCES weeks(id),
  date TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, distance_km REAL, minutes INTEGER,
  target_kind TEXT, target_low REAL, target_high REAL, status TEXT NOT NULL DEFAULT 'planned',
  schedule_id TEXT, workout_id TEXT, updated_at INTEGER NOT NULL);
CREATE INDEX sessions_date ON sessions (date);
CREATE TABLE steps (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, position INTEGER NOT NULL,
  kind TEXT NOT NULL, parent_id TEXT, times INTEGER, duration_s INTEGER, distance_m REAL,
  target_kind TEXT, target_low REAL, target_high REAL, added_auto INTEGER NOT NULL DEFAULT 0);
CREATE INDEX steps_session ON steps (session_id, position);

CREATE TABLE watch_operations (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN (${list(WATCH_OPERATION_TYPES)})),
  target TEXT NOT NULL,
  session_id TEXT,
  state TEXT NOT NULL DEFAULT 'not_sent' CHECK (state IN (${list(WATCH_STATES)})),
  confirmed_at INTEGER, sent_at INTEGER, on_watch_at INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER, error TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX watch_operations_state ON watch_operations (state, next_attempt_at);

CREATE TABLE activities (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, source_id TEXT NOT NULL, start_time INTEGER NOT NULL,
  distance_m REAL, duration_s INTEGER, type TEXT, title TEXT, session_id TEXT,
  hidden_by_merge TEXT, updated_at INTEGER NOT NULL, UNIQUE (provider, source_id));
CREATE INDEX activities_start ON activities (start_time);
CREATE TABLE splits (
  activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE, position INTEGER NOT NULL,
  distance_m REAL, duration_s REAL, hr_avg REAL, PRIMARY KEY (activity_id, position));
CREATE TABLE streams (
  activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  metric TEXT NOT NULL CHECK (metric IN (${list(STREAM_METRICS)})),
  data TEXT NOT NULL, PRIMARY KEY (activity_id, metric));

CREATE TABLE sleep_nights (date TEXT PRIMARY KEY, provider TEXT NOT NULL, minutes INTEGER, updated_at INTEGER NOT NULL);
CREATE TABLE hrv_nights (date TEXT PRIMARY KEY, provider TEXT NOT NULL, ms REAL, updated_at INTEGER NOT NULL);

CREATE TABLE connections (
  provider TEXT PRIMARY KEY, route TEXT NOT NULL, capabilities TEXT NOT NULL, state TEXT NOT NULL,
  last_sync INTEGER, priority INTEGER NOT NULL DEFAULT 0);
CREATE TABLE merge_records (
  id TEXT PRIMARY KEY, kept_activity_id TEXT NOT NULL, hidden_activity_id TEXT NOT NULL,
  reason TEXT NOT NULL, created_at INTEGER NOT NULL, undone_at INTEGER);

CREATE TABLE coach_messages (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
  context_tag TEXT, sources TEXT, status TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE INDEX coach_messages_thread ON coach_messages (thread_id, created_at);
CREATE TABLE memory_items (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE entitlements (product TEXT PRIMARY KEY, active INTEGER NOT NULL, expires_at INTEGER, updated_at INTEGER NOT NULL);
`,
  },
];
