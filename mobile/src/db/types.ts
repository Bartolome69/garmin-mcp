/** The few calls the app makes on SQLite, so the same code runs on expo-sqlite and in tests. */
export type SqlParams = (string | number | null)[];
export interface Db {
  exec(sql: string): Promise<void>;
  run(sql: string, params?: SqlParams): Promise<void>;
  get<T>(sql: string, params?: SqlParams): Promise<T | null>;
  all<T>(sql: string, params?: SqlParams): Promise<T[]>;
}
