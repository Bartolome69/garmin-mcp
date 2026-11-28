// UNVERIFIED: written without Xcode and never built or run. Needs a device build
// (docs/native-app/DECISIONS.md, milestone 10) before it ships.
//
// Daash's iOS widgets (build prompt, section 5.15; board P1-Widgets-ios):
//   Today      small: readiness ring, decision, today's session, watch status
//              lock screen: circular readiness ring, rectangular session
//   Countdown  small: days to the race
//   Week       medium: the week strip
// Every widget reads the snapshot the app writes (mobile/src/features/widgets/
// snapshot.ts) from the App Group; none asks the server. Never a blank: no
// snapshot or Free says so in words, and old data shows "Updated <time>".
// Colours come from DaashTokens.swift, generated from the design tokens at prebuild.
import SwiftUI
import WidgetKit

let appGroup = "group.run.daash.app"
let snapshotKey = "daash.widget.snapshot"   // bridge.ts SNAPSHOT_KEY
let staleAfter: TimeInterval = 30 * 60       // freshness.ts STALE_AFTER_MS

// MARK: - The snapshot (keep in step with WidgetSnapshot in snapshot.ts)

struct Snapshot: Codable {
  struct Readiness: Codable { let score: Int?; let word: String; let decision: String }
  struct Session: Codable { let type: String; let typeLabel: String; let line: String; let title: String; let watch: String; let watchState: String }
  struct Countdown: Codable { let race: String; let days: Int; let date: String; let raceDay: String }
  struct Day: Codable { let letter: String; let day: Int; let type: String; let label: String; let today: Bool; let done: Bool }
  struct Week: Codable { let title: String; let summary: String; let days: [Day] }
  struct Circular: Codable { let score: Int?; let word: String }
  struct Rectangular: Codable { let line1: String; let line2: String }
  struct Lock: Codable { let circular: Circular?; let rectangular: Rectangular? }

  let v: Int
  let writtenAt: Double
  let dataAt: Double?
  let updated: String?
  let locked: Bool
  let message: String?
  let readiness: Readiness?
  let session: Session?
  let countdown: Countdown?
  let week: Week?
  let lock: Lock
}

func loadSnapshot() -> Snapshot? {
  guard let json = UserDefaults(suiteName: appGroup)?.string(forKey: snapshotKey),
        let data = json.data(using: .utf8) else { return nil }
  return try? JSONDecoder().decode(Snapshot.self, from: data)
}

/// The same rule as snapshot.ts updatedLabel, worked out when the widget is drawn.
func updatedLabel(_ s: Snapshot, now: Date) -> String? {
  guard let ms = s.dataAt else { return nil }
  let at = Date(timeIntervalSince1970: ms / 1000)
  guard now.timeIntervalSince(at) >= staleAfter else { return nil }
  let cal = Calendar.current
  let gap = cal.dateComponents([.day], from: cal.startOfDay(for: at), to: cal.startOfDay(for: now)).day ?? 0
  let f = DateFormatter()
  f.locale = Locale(identifier: "en_GB")
  if gap <= 0 { f.dateFormat = "HH:mm"; return "Updated \(f.string(from: at))" }
  if gap == 1 { return "Updated yesterday" }
  f.dateFormat = "d MMM"
  return "Updated \(f.string(from: at))"
}

/// Days to race day from today, so the countdown moves without the app.
func daysTo(_ c: Snapshot.Countdown, now: Date) -> Int {
  let f = DateFormatter()
  f.dateFormat = "yyyy-MM-dd"
  guard let race = f.date(from: c.raceDay) else { return c.days }
  let cal = Calendar.current
  return max(0, cal.dateComponents([.day], from: cal.startOfDay(for: now), to: cal.startOfDay(for: race)).day ?? c.days)
}

func decisionColor(_ d: String) -> Color {
  switch d {
  case "push": return .daashSuccess
  case "back_off": return .daashWarning
  default: return .daashText2
  }
}

func typeColor(_ t: String) -> Color {
  switch t {
  case "easy", "recovery": return .daashCEasy
  case "tempo": return .daashCTempo
  case "intervals": return .daashCInt
  case "long": return .daashCLong
  case "race": return .daashRace
  case "strength": return .daashCStr
  case "rest": return .daashCRest
  default: return .daashMuted
  }
}

// MARK: - Timeline

struct Entry: TimelineEntry {
  let date: Date
  let snapshot: Snapshot?
}

struct Provider: TimelineProvider {
  func placeholder(in context: Context) -> Entry { Entry(date: Date(), snapshot: nil) }

  func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
    completion(Entry(date: Date(), snapshot: loadSnapshot()))
  }

  func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
    let now = Date()
    let snapshot = loadSnapshot()
    // An entry every 30 minutes for 6 hours: "Updated" and the countdown change without the app.
    let entries = (0..<12).map { Entry(date: now.addingTimeInterval(Double($0) * 1800), snapshot: snapshot) }
    completion(Timeline(entries: entries, policy: .atEnd))
  }
}

// MARK: - Views

struct Message: View {
  let text: String
  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text("Daash").font(.caption2.weight(.semibold)).foregroundStyle(Color.daashMuted)
      Text(text).font(.footnote).foregroundStyle(Color.daashText)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
  }
}

struct Ring: View {
  let score: Int?
  let decision: String
  var body: some View {
    ZStack {
      Circle().stroke(Color.daashHairline, lineWidth: 5)
      Circle().trim(from: 0, to: CGFloat(min(100, max(0, score ?? 0))) / 100)
        .stroke(decisionColor(decision), style: StrokeStyle(lineWidth: 5, lineCap: .round))
        .rotationEffect(.degrees(-90))
      Text(score.map(String.init) ?? "–").font(.headline.monospacedDigit()).foregroundStyle(Color.daashText)
    }
  }
}

struct Updated: View {
  let snapshot: Snapshot
  let now: Date
  var body: some View {
    if let label = updatedLabel(snapshot, now: now) {
      Text(label).font(.caption2).foregroundStyle(Color.daashMuted)
    }
  }
}

struct TodaySmall: View {
  let s: Snapshot
  let now: Date
  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      if let r = s.readiness {
        HStack(spacing: 8) {
          Ring(score: r.score, decision: r.decision).frame(width: 44, height: 44)
          VStack(alignment: .leading, spacing: 0) {
            Text("Readiness").font(.caption2).foregroundStyle(Color.daashMuted)
            Text(r.word).font(.subheadline.weight(.bold)).foregroundStyle(decisionColor(r.decision))
          }
        }
      }
      if let x = s.session {
        HStack(spacing: 6) {
          RoundedRectangle(cornerRadius: 1.5).fill(typeColor(x.type)).frame(width: 3)
          VStack(alignment: .leading, spacing: 1) {
            Text(x.line).font(.caption2).foregroundStyle(Color.daashMuted)
            Text(x.title).font(.subheadline.weight(.semibold)).foregroundStyle(Color.daashText).lineLimit(1)
            Text(x.watch).font(.caption2).foregroundStyle(Color.daashText2)
          }
        }
      } else {
        Text("Rest day").font(.subheadline).foregroundStyle(Color.daashText)
      }
      Spacer(minLength: 0)
      Updated(snapshot: s, now: now)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
  }
}

struct TodayView: View {
  @Environment(\.widgetFamily) var family
  let entry: Entry
  var body: some View {
    let s = entry.snapshot
    switch family {
    case .accessoryCircular:
      if let c = s?.lock.circular {
        Gauge(value: Double(c.score ?? 0), in: 0...100) { Text("Readiness") } currentValueLabel: { Text(c.score.map(String.init) ?? "–") }
          .gaugeStyle(.accessoryCircularCapacity)
          .accessibilityLabel("Readiness \(c.score.map(String.init) ?? ""), \(c.word)")
      } else {
        Image(systemName: "figure.run").accessibilityLabel("Daash")
      }
    case .accessoryRectangular:
      VStack(alignment: .leading) {
        if let r = s?.lock.rectangular {
          Text(r.line1).font(.headline).lineLimit(1)
          Text(r.line2).font(.caption).lineLimit(1)
          if let s, let label = updatedLabel(s, now: entry.date) { Text(label).font(.caption2) }
        } else {
          Text(s?.message ?? "Open Daash to load today.").font(.caption)
        }
      }
    default:
      if let s, s.message == nil { TodaySmall(s: s, now: entry.date) }
      else { Message(text: entry.snapshot?.message ?? "Open Daash to load today.") }
    }
  }
}

struct CountdownView: View {
  let entry: Entry
  var body: some View {
    if let s = entry.snapshot, let c = s.countdown, s.message == nil {
      VStack(alignment: .leading, spacing: 2) {
        Text(c.race).font(.caption).foregroundStyle(Color.daashMuted)
        Text("\(daysTo(c, now: entry.date))").font(.largeTitle.weight(.bold).monospacedDigit()).foregroundStyle(Color.daashText)
        Text("days · \(c.date)").font(.caption2).foregroundStyle(Color.daashMuted)
        Spacer(minLength: 0)
        Updated(snapshot: s, now: entry.date)
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    } else {
      Message(text: entry.snapshot?.message ?? "Open Daash to load today.")
    }
  }
}

struct WeekView: View {
  let entry: Entry
  var body: some View {
    if let s = entry.snapshot, let w = s.week, s.message == nil {
      VStack(alignment: .leading, spacing: 6) {
        Text(w.title).font(.caption).foregroundStyle(Color.daashMuted)
        Text(w.summary).font(.subheadline.weight(.semibold)).foregroundStyle(Color.daashText)
        HStack(spacing: 4) {
          ForEach(Array(w.days.enumerated()), id: \.offset) { _, d in
            VStack(spacing: 2) {
              RoundedRectangle(cornerRadius: 1.5).fill(typeColor(d.type)).frame(height: 3)
              Text(d.letter).font(.caption2).foregroundStyle(Color.daashMuted)
              Text("\(d.day)").font(.caption2.monospacedDigit().weight(d.today ? .bold : .regular)).foregroundStyle(Color.daashText)
              Text(d.label).font(.caption2).foregroundStyle(d.done ? Color.daashMuted : Color.daashText).lineLimit(1)
            }
            .padding(.vertical, 2)
            .background(d.today ? Color.daashSunken : Color.clear, in: RoundedRectangle(cornerRadius: 6))
          }
        }
        Spacer(minLength: 0)
        Updated(snapshot: s, now: entry.date)
      }
    } else {
      Message(text: entry.snapshot?.message ?? "Open Daash to load today.")
    }
  }
}

// MARK: - Widgets

struct TodayWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "DaashToday", provider: Provider()) { entry in
      TodayView(entry: entry).containerBackground(Color.daashSurface, for: .widget)
    }
    .configurationDisplayName("Today")
    .description("Readiness, today’s session and your watch.")
    .supportedFamilies([.systemSmall, .accessoryCircular, .accessoryRectangular])
  }
}

struct CountdownWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "DaashCountdown", provider: Provider()) { entry in
      CountdownView(entry: entry).containerBackground(Color.daashSurface, for: .widget)
    }
    .configurationDisplayName("Race countdown")
    .description("Days to your race.")
    .supportedFamilies([.systemSmall])
  }
}

struct WeekWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "DaashWeek", provider: Provider()) { entry in
      WeekView(entry: entry).containerBackground(Color.daashSurface, for: .widget)
    }
    .configurationDisplayName("This week")
    .description("Your week, day by day.")
    .supportedFamilies([.systemMedium])
  }
}

@main
struct DaashWidgetBundle: WidgetBundle {
  var body: some Widget {
    TodayWidget()
    CountdownWidget()
    WeekWidget()
  }
}
