// UNVERIFIED: written without Xcode and never built. Needs a device build
// (docs/native-app/DECISIONS.md, milestone 10) before it ships.
//
// The app's half of the widgets (mobile/src/features/widgets/bridge.ts): write
// the snapshot JSON to the App Group's shared defaults, where the widget
// extension reads it, then ask WidgetKit to redraw every Daash widget.
import ExpoModulesCore
import WidgetKit

let daashAppGroup = "group.run.daash.app"

public class DaashWidgetsModule: Module {
  public func definition() -> ModuleDefinition {
    Name("DaashWidgets")

    AsyncFunction("write") { (key: String, json: String) in
      guard let defaults = UserDefaults(suiteName: daashAppGroup) else {
        throw Exception(name: "NoAppGroup", description: "The App Group \(daashAppGroup) isn't set up for this build.")
      }
      defaults.set(json, forKey: key)
    }

    AsyncFunction("reload") {
      WidgetCenter.shared.reloadAllTimelines()
    }
  }
}
