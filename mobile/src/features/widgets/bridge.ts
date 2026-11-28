// The app's side of the widgets: write the snapshot where the widgets can read it
// (an App Group's shared defaults on iOS, the app's shared preferences on Android),
// then ask the system to redraw them (WidgetKit's reloadAllTimelines, Glance's
// updateAll). The native half (an Expo module, DaashWidgets) is in
// mobile/native/widgets/, not yet in the build: it needs a device build to verify
// (DECISIONS, milestone 10). Until it's there, writes go nowhere, quietly.
import { requireOptionalNativeModule } from 'expo';

import type { WidgetSnapshot } from './snapshot';

export interface WidgetBridge {
  write(snapshot: WidgetSnapshot): Promise<void>;
  reload(): Promise<void>;
}

/** For development and tests: keeps what was written. */
export class FakeWidgetBridge implements WidgetBridge {
  written: WidgetSnapshot[] = [];
  reloads = 0;
  async write(snapshot: WidgetSnapshot) { this.written.push(snapshot); }
  async reload() { this.reloads += 1; }
  get last(): WidgetSnapshot | null { return this.written[this.written.length - 1] ?? null; }
}

/** The key both sides use. Keep in step with DaashWidgetsModule (Swift and Kotlin). */
export const SNAPSHOT_KEY = 'daash.widget.snapshot';

type NativeWidgets = { write(key: string, json: string): Promise<void>; reload(): Promise<void> };

/** The native module, when the build has it (mobile/native/widgets/). */
export class NativeWidgetBridge implements WidgetBridge {
  constructor(private native: NativeWidgets) {}
  async write(snapshot: WidgetSnapshot) { await this.native.write(SNAPSHOT_KEY, JSON.stringify(snapshot)); }
  async reload() { await this.native.reload(); }
}

const nothing: WidgetBridge = { write: async () => {}, reload: async () => {} };

export function defaultWidgetBridge(): WidgetBridge {
  const native = requireOptionalNativeModule<NativeWidgets>('DaashWidgets');
  return native ? new NativeWidgetBridge(native) : nothing;
}
