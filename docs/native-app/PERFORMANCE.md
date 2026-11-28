# Native app: performance (milestone 12)

What was changed, what's pinned by a test, and what still needs measuring on a phone.
Nothing here was timed on a device: this container has no simulator or phone. Jest can
count renders and recomputations; frame times, memory and start-up need a device.

## Changed, and pinned by tests (`src/perf.test.tsx`)

| Where | Before | Now | Test |
|---|---|---|---|
| **Today during a watch-queue tick** | Every change to the queue (any session's send state) built a new Today view model and redrew the whole screen. | `TodayView` is memoised. The screen keeps the same view-model object while what it draws is the same (`useStable`, compared by value), and the same actions object (`useStableActions`). An idle tick, or a send for a session that isn't on Today, doesn't redraw it. A change to today's session does. | *an idle tick and a send for another week leave TodayView alone; a change to today redraws it* |
| **Coach streaming** | Each streamed token re-rendered the whole thread (a `ScrollView` mapping every message). | The thread is a `FlatList` of memoised rows (`Question`, `Reply`, `Card`, `MemoryNote`), with stable actions and a value comparison for proposal cards. The thread model already replaced only the streaming message per token, so each token redraws one message row; the earlier rows are skipped. The screen, the list's shell, the composer dock and the header above the thread still re-render on every token (their render functions are rebuilt each time): cheap, and not pinned. | *each token redraws only the reply it belongs to* (seven tokens: the three earlier messages render 0 more times) |
| **Coach thread length** | All messages mounted. | Virtualised (`initialNumToRender` 20, `windowSize` 11). Days before today still fold into "Earlier", so the list is short in practice. | *the thread is a virtualised list* |
| **Post-run scrubbing** | Every scrub step (finger or adjustable action) rebuilt the route map's path and every chart line from the full series. | `RouteMap` is memoised (its path depends only on the route); chart lines are worked out once per run and metric (`linePoints`, `useMemo`); a scrub moves the marker and label only. | *scrubbing moves the marker without redrawing the map or working out the lines again* |
| **Update size** | The font packages' index files pulled in every weight (about 60 font files) into each bundle and EAS update. | The root layout imports the three faces used (`GeistMono_500Medium`, `SourceSerif4_400Regular`, `…_Italic`) by path. Matters for the owner's test on mobile data. | `npx expo export` lists the assets |

`useRenderCount(name)` (`src/ui/hooks.ts`) counts renders in Jest only; it does nothing in
the app.

## Looked at, left as they are

- **Plan · Week** is already a horizontal `FlatList` of weeks (paged), each week 7 rows.
  **Plan · Block** draws one row per week of the block (about 20) and **Trends** a handful
  of points; neither is long enough to virtualise.
- **Post-run laps** are at most a few dozen rows inside the screen's `ScrollView`. A
  virtualised list can't nest inside a vertical `ScrollView` (React Native warns and drops
  virtualisation), and turning the screen into one `FlatList` with the map and charts as
  header isn't worth it at this length. Revisit if runs with 100+ laps appear (track
  sessions recorded lap by lap).
- **Chart paths elsewhere** (the readiness signal chart, the prediction trend, inline Coach
  charts) are 7 to 14 points and redraw only when their data changes.
- **Lazy-loading screens:** expo-router's lazy bundling (`asyncRoutes`) is for web and
  development in SDK 58; a native production bundle (and an EAS update) is one file, so
  there's nothing to split per route. Heavy modules that aren't always needed are already
  loaded on first use: `expo/fetch` for streaming, `expo-notifications` for the permission
  and reminders.
- **NativeTabs `activityEnabled`** (inactive tabs hidden with React Activity, keeping state)
  would cut work in tabs you aren't on; it's off until it can be checked on a device that
  state and the Coach's provider survive it.

## To measure on a device

Use a release build (`eas build --profile preview`), not Expo Go or a development build
(both run unoptimised JavaScript), on a mid-range Android (e.g. a Pixel 6a) and an older
iPhone (e.g. an iPhone 12).

1. **Cold start to Today drawn** with a cached day (SQLite read first): target under 1.5 s on
   the Android. Expo's `expo-updates` adds an update check at launch in production; check it
   doesn't block the first frame (`fallbackToCacheTimeout` stays 0, the default).
2. **Scrolling** Today, Plan · Week (swiping weeks), Plan · Block and a post-run screen: no
   dropped frames in the React Native perf monitor / Android GPU profiling.
3. **Coach streaming** a long reply (500+ words) in a thread of 200 messages: the JS frame
   rate stays near 60 and typing in the composer doesn't lag. If it does, batch the stream's
   deltas (e.g. one commit per animation frame) in `CoachProvider`.
4. **Watch queue** with 10 confirmed operations sending and polling: Today and Plan stay
   responsive; the 5 s pump and 30 s poll don't show in a JS profile.
5. **Memory** after 10 minutes moving between tabs with the Coach on: no steady growth.
6. **Large text** (200%) and **wide windows** (an Android tablet): layout passes per frame on
   Today and Plan, since cards grow and the column is capped at 680 pt.
7. **The keyboard** with the Coach: the composer follows the keyboard's animation without a
   jump on both platforms (Android: with and without edge to edge).

Record the numbers here when they're taken.
