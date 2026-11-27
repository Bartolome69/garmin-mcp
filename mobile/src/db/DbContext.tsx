import { createContext, useContext } from 'react';

import type { Db } from './types';

/** The app's local database, once open; null before then (and in tests that don't need one). */
export const DbContext = createContext<Db | null>(null);
export const useDb = () => useContext(DbContext);
