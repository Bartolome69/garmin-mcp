// UNVERIFIED: written without Android Studio and never built or run. Needs a device
// build (docs/native-app/DECISIONS.md, milestone 10) before it ships.
//
// Daash's Android widgets in Glance (build prompt, section 5.15; board
// P1-Widgets-android): 2×2 Today (readiness ring, decision, today's session, watch
// status), 4×2 week strip, and the race countdown. Every widget reads the snapshot
// the app writes (mobile/src/features/widgets/snapshot.ts); none asks the server.
// Never a blank: no snapshot or Free says so in words, and old data shows
// "Updated <time>". Material You may tint the surface; the type colours and the
// text stay (board). Colours come from DaashTokens.kt, generated at prebuild.
package run.daash.widgets

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.semantics.contentDescription
import androidx.glance.semantics.semantics
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.concurrent.TimeUnit

const val SNAPSHOT_KEY = "daash.widget.snapshot" // bridge.ts SNAPSHOT_KEY
const val STALE_AFTER_MS = 30 * 60 * 1000L        // freshness.ts STALE_AFTER_MS
const val EMPTY = "Open Daash to load today."

/** The snapshot, read leniently (keep in step with WidgetSnapshot in snapshot.ts). */
class Snapshot(private val o: JSONObject) {
  val message: String? get() = o.optString("message").takeIf { !o.isNull("message") && it.isNotEmpty() }
  val dataAt: Long? get() = if (o.isNull("dataAt")) null else o.optLong("dataAt")
  val readiness: JSONObject? get() = o.optJSONObject("readiness")
  val session: JSONObject? get() = o.optJSONObject("session")
  val countdown: JSONObject? get() = o.optJSONObject("countdown")
  val week: JSONObject? get() = o.optJSONObject("week")

  /** The same rule as snapshot.ts updatedLabel, worked out when drawn. */
  fun updated(now: Long = System.currentTimeMillis()): String? {
    val at = dataAt ?: return null
    if (now - at < STALE_AFTER_MS) return null
    val gap = dayNumber(now) - dayNumber(at)
    return when {
      gap <= 0 -> "Updated " + SimpleDateFormat("HH:mm", Locale.UK).format(at)
      gap == 1L -> "Updated yesterday"
      else -> "Updated " + SimpleDateFormat("d MMM", Locale.UK).format(at)
    }
  }

  companion object {
    fun load(context: Context): Snapshot? = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      .getString(SNAPSHOT_KEY, null)?.let { runCatching { Snapshot(JSONObject(it)) }.getOrNull() }

    private fun dayNumber(t: Long): Long {
      val c = Calendar.getInstance().apply { timeInMillis = t }
      return TimeUnit.MILLISECONDS.toDays(t + c.get(Calendar.ZONE_OFFSET) + c.get(Calendar.DST_OFFSET))
    }

    /** Days to race day from today, so the countdown moves without the app. */
    fun daysTo(raceDay: String, fallback: Int): Int = runCatching {
      val race = SimpleDateFormat("yyyy-MM-dd", Locale.UK).parse(raceDay)!!.time
      maxOf(0L, dayNumber(race) - dayNumber(System.currentTimeMillis())).toInt()
    }.getOrDefault(fallback)
  }
}

fun decisionColor(d: String?): ColorProvider = when (d) {
  "push" -> DaashTokens.success
  "back_off" -> DaashTokens.warning
  else -> DaashTokens.text2
}

fun typeColor(t: String?): ColorProvider = when (t) {
  "easy", "recovery" -> DaashTokens.cEasy
  "tempo" -> DaashTokens.cTempo
  "intervals" -> DaashTokens.cInt
  "long" -> DaashTokens.cLong
  "race" -> DaashTokens.race
  "strength" -> DaashTokens.cStr
  "rest" -> DaashTokens.cRest
  else -> DaashTokens.muted
}

@Composable
fun Surface(description: String, content: @Composable () -> Unit) {
  Box(
    GlanceModifier.fillMaxSize().background(GlanceTheme.colors.widgetBackground).cornerRadius(24.dp).padding(12.dp)
      .semantics { contentDescription = description },
  ) { content() }
}

@Composable
fun Message(text: String) = Surface(text) {
  Column {
    Text("Daash", style = TextStyle(color = DaashTokens.muted, fontSize = 12.sp, fontWeight = FontWeight.Medium))
    Text(text, style = TextStyle(color = DaashTokens.text, fontSize = 14.sp))
  }
}

@Composable
fun UpdatedLine(s: Snapshot) {
  s.updated()?.let { Text(it, style = TextStyle(color = DaashTokens.muted, fontSize = 11.sp)) }
}

class TodayWidget : GlanceAppWidget() {
  override suspend fun provideGlance(context: Context, id: GlanceId) {
    val s = Snapshot.load(context)
    provideContent {
      GlanceTheme {
        if (s == null || s.message != null) Message(s?.message ?: EMPTY) else {
          val r = s.readiness
          val x = s.session
          val words = listOfNotNull(
            r?.let { "Readiness ${it.optString("score")} ${it.optString("word")}" },
            x?.let { "${it.optString("line")}, ${it.optString("title")}, ${it.optString("watch")}" },
            s.updated(),
          ).joinToString(". ")
          Surface(words) {
            Column {
              if (r != null) {
                Text("Readiness", style = TextStyle(color = DaashTokens.muted, fontSize = 11.sp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                  // Glance has no arc: the score and the word, in the decision's colour, carry it.
                  Text(r.optString("score", "–"), style = TextStyle(color = DaashTokens.text, fontSize = 26.sp, fontWeight = FontWeight.Bold))
                  Spacer(GlanceModifier.width(8.dp))
                  Text(r.optString("word"), style = TextStyle(color = decisionColor(r.optString("decision")), fontSize = 14.sp, fontWeight = FontWeight.Bold))
                }
              }
              Spacer(GlanceModifier.height(6.dp))
              if (x != null) {
                Row {
                  Box(GlanceModifier.width(3.dp).height(44.dp).background(typeColor(x.optString("type")))) {}
                  Spacer(GlanceModifier.width(6.dp))
                  Column {
                    Text(x.optString("line"), style = TextStyle(color = DaashTokens.muted, fontSize = 11.sp))
                    Text(x.optString("title"), style = TextStyle(color = DaashTokens.text, fontSize = 14.sp, fontWeight = FontWeight.Medium), maxLines = 1)
                    Text(x.optString("watch"), style = TextStyle(color = DaashTokens.text2, fontSize = 11.sp))
                  }
                }
              } else Text("Rest day", style = TextStyle(color = DaashTokens.text, fontSize = 14.sp))
              UpdatedLine(s)
            }
          }
        }
      }
    }
  }
}

class WeekWidget : GlanceAppWidget() {
  override suspend fun provideGlance(context: Context, id: GlanceId) {
    val s = Snapshot.load(context)
    provideContent {
      GlanceTheme {
        val w = s?.week
        if (s == null || s.message != null || w == null) Message(s?.message ?: EMPTY) else {
          val days = w.optJSONArray("days")
          val words = "${w.optString("title")}. ${w.optString("summary")}"
          Surface(words) {
            Column {
              Text(w.optString("title"), style = TextStyle(color = DaashTokens.muted, fontSize = 11.sp))
              Text(w.optString("summary"), style = TextStyle(color = DaashTokens.text, fontSize = 14.sp, fontWeight = FontWeight.Medium))
              Spacer(GlanceModifier.height(6.dp))
              Row(GlanceModifier.fillMaxWidth()) {
                for (i in 0 until (days?.length() ?: 0)) {
                  val d = days!!.getJSONObject(i)
                  Column(GlanceModifier.defaultWeight().padding(2.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(GlanceModifier.fillMaxWidth().height(3.dp).background(typeColor(d.optString("type")))) {}
                    Text(d.optString("letter"), style = TextStyle(color = DaashTokens.muted, fontSize = 11.sp))
                    Text(d.optInt("day").toString(), style = TextStyle(color = DaashTokens.text, fontSize = 11.sp,
                      fontWeight = if (d.optBoolean("today")) FontWeight.Bold else FontWeight.Normal))
                    Text(d.optString("label"), style = TextStyle(color = if (d.optBoolean("done")) DaashTokens.muted else DaashTokens.text, fontSize = 11.sp), maxLines = 1)
                  }
                }
              }
              UpdatedLine(s)
            }
          }
        }
      }
    }
  }
}

class CountdownWidget : GlanceAppWidget() {
  override suspend fun provideGlance(context: Context, id: GlanceId) {
    val s = Snapshot.load(context)
    provideContent {
      GlanceTheme {
        val c = s?.countdown
        if (s == null || s.message != null || c == null) Message(s?.message ?: EMPTY) else {
          val days = Snapshot.daysTo(c.optString("raceDay"), c.optInt("days"))
          Surface("${c.optString("race")}, $days days, ${c.optString("date")}") {
            Column {
              Text(c.optString("race"), style = TextStyle(color = DaashTokens.muted, fontSize = 12.sp))
              Text(days.toString(), style = TextStyle(color = DaashTokens.text, fontSize = 34.sp, fontWeight = FontWeight.Bold))
              Text("days · ${c.optString("date")}", style = TextStyle(color = DaashTokens.muted, fontSize = 11.sp))
              UpdatedLine(s)
            }
          }
        }
      }
    }
  }
}

class TodayWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = TodayWidget() }
class WeekWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = WeekWidget() }
class CountdownWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = CountdownWidget() }
