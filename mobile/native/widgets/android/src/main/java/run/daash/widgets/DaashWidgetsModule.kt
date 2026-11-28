// UNVERIFIED: written without Android Studio and never built. Needs a device build
// (docs/native-app/DECISIONS.md, milestone 10) before it ships.
//
// The app's half of the widgets (mobile/src/features/widgets/bridge.ts): write the
// snapshot JSON to shared preferences, where the Glance widgets read it, then ask
// Glance to redraw every Daash widget.
package run.daash.widgets

import android.content.Context
import androidx.glance.appwidget.updateAll
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

const val PREFS = "daash.widgets"

class DaashWidgetsModule : Module() {
  private val context: Context
    get() = requireNotNull(appContext.reactContext) { "No React context" }

  override fun definition() = ModuleDefinition {
    Name("DaashWidgets")

    AsyncFunction("write") { key: String, json: String ->
      context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(key, json).apply()
    }

    AsyncFunction("reload") {
      val ctx = context.applicationContext
      CoroutineScope(Dispatchers.Default).launch {
        TodayWidget().updateAll(ctx)
        WeekWidget().updateAll(ctx)
        CountdownWidget().updateAll(ctx)
      }
    }
  }
}
