# Screen reader walkthroughs (VoiceOver and TalkBack)

**Status: every flow below needs a device run.** None of this has been heard on a phone
yet. What's checked in Jest is what the screen reader *should* find:

- `src/focus.test.tsx` walks every screen: reading order (what the screen is before any
  action), a name on everything focusable, no two controls with the same name, the primary
  action reachable, sheets presented as native sheets;
- `src/__matrix__/*.txt` (from `src/matrix/`) is the full list of what each screen offers a
  screen reader, in order, in every theme, text size and Coach state. The lines a tester
  should hear below come from those files.

Run each flow on an iPhone with VoiceOver and an Android phone with TalkBack, at the
default text size and again at the largest size (Settings → Accessibility → Larger Text /
Font size), in light and dark. Swipe right (next) through each screen from the top unless a
step says otherwise. Use sample data (Expo Go, or `EXPO_PUBLIC_DAASH_FIXTURE=1`; see
`EXPO_GO.md`): the numbers below are the London persona's. Write down anything that's said
differently, said twice, skipped, or can't be reached.

How the words are written: *"…"* is what should be said. VoiceOver adds the role ("button",
"heading", "selected"); TalkBack adds "button", "heading", "double-tap to activate". Those
are left out below unless they matter.

## 0. Every screen

- The tab bar: *"Today, tab, 1 of 4"* (5 with the Coach on). The selected tab says
  *"selected"*. With the keyboard up the tab bar is hidden and can't be reached.
- The first thing on each screen is its title or what it is, never a button (a modal's
  *"Close"* first is the system's pattern).
- Section titles are headings: the rotor (iOS) / heading navigation (TalkBack) moves
  between them.
- A sheet keeps focus inside it; the screen behind is not reachable. **Escape** (iOS
  two-finger scrub, or a keyboard's Esc) and Android **back** close it.
- Charts are one element with a one-line summary, e.g. *"HRV over the last 14 nights. Your
  range 54–66 ms. Last night 49 · below."*
- With **Reduce Motion** on (iOS) / **Remove animations** (Android), the Plan week changes in
  place instead of sliding.

## 1. Today → Make it easier → confirm → on the watch

1. Today: *"Fri 27 Nov · Week 4 of 21"*, the time it was last updated, *"London in 148 days"*
   (heading), *"Garmin readiness 26"*, *"Back off"*, *"Garmin readiness 26 · 2 of 3 below
   usual"*, *"How it works"*, then the three signals: *"Sleep 4:45, 2h under. Opens details"*,
   *"HRV 49 ms, below range. Opens details"*, *"Load 1.12, in range. Opens details"*, the
   explanation, *"Ask why"* (Coach on only), *"Make it easier"*, *"Other options"*, then the
   session (*"Tempo"*, *"On your watch · 20:14"*, *"3 × 2 km at threshold"*, *"Today · 10 km ·
   about 52 min · 4:23–4:28 /km"*), the structure once, as one image (*"Session structure:
   Warm-up 15 min, 3 × 2 km · 2 min jog, Cool 10"*; the words drawn under the bar aren't read
   again), *"This week"*, *"28 of 54 km · 11 active weeks"*, and the week strip (*"Rest, 23,
   rest day"*, *"Easy, 24, done"* … *"Tempo, 27, planned, today"* … *"Long run, 29,
   planned"*).
2. Double-tap *"Make it easier"*. The new week opens: *"New week"* (heading), what changes
   (each day as one element: *"FRI 27: 3 × 2 km at threshold, becomes Easy …"*), *"Rule used"*
   (heading) and the rule, then *"Review watch changes"*.
3. Double-tap it. The watch confirm sheet: *"1 change on your watch"* (heading), *"This is
   exactly what Forerunner 965 gets. Nothing else on the watch changes."*, the change (*"Replace,
   Fri 27, …"*), *"Connected to Garmin · synced 07:02"*, *"Confirm and send 1 change"*, *"Back"*.
   Focus can't leave the sheet.
4. Double-tap *"Confirm and send 1 change"*. Back on Today you should hear each change of the
   send announced once, without moving focus: *"Sending to Forerunner 965"*, then *"Changed ·
   arrives at next sync. Changed your mind? Remove from watch."*, then *"On your watch"* when
   the watch syncs. Each is said once, even with Plan or the Coach open in another tab.
5. The toast *"Changed your mind? · Remove from watch"* appears; with a screen reader on it
   stays until dismissed (it doesn't time out).

## 2. Life happened → new week → confirm

1. Plan: *"Plan"* (heading), *"Plan view, Week"* (the Week / Block / Trends control),
   *"Week 4 · Base"* (heading), *"Previous week"*, *"This week"*, *"Next week"*, the week line,
   this week's seven days only (*"Mon 23, Rest day, Rest · Recovery, Done"*, *"Tue 24, 8 km easy
   + strides, Easy · 8.1 km · 5:12 per km, Done"* … *"Sun 29, Long 18 km easy, …"*), *"Swipe for
   other weeks. Hold and drag a session to move it."*, then *"Life happened"* and *"New
   workout"* last. The weeks either side are drawn for swiping but not read; *"Next week"*
   brings the next one's days, under its own heading.
2. Double-tap *"Life happened"*. The sheet: *"What changed?"* (heading), the reasons as
   radio buttons, one *"selected"*: *"Not feeling 100%. Cold, tired, run down"*, *"Niggle or
   injury. Something hurts when you run"*, …; *"Since"*, *"Today"* (selected), *"Yesterday"*,
   *"Pick a date"*, *"See the new week"*, and the medical line *"Daash doesn't give medical
   advice. If you're unwell or hurt, check with a doctor or physio."*
3. *"See the new week"* opens the diff, then *"Review watch changes"* → the same confirm as
   flow 1. **Back** on the confirm says nothing was sent and stays on the diff.

## 3. Send fails → Resend

Use `EXPO_PUBLIC_DAASH_WATCH=fail` (the stand-in watch fails its first try).

1. Send a session from its detail (*"Send to watch"*) and confirm.
2. You should hear *"Not sent. Garmin didn't answer."* announced, and on the card: the reason
   in words, *"Resend"*, *"Trouble connecting?"*, *"Nothing changed on your watch."* No
   exclamation marks; the colour is never the only sign (an icon and the words). A retry Daash
   makes on its own that fails the same way isn't announced again.
3. Double-tap *"Resend"*: *"Sending to …"*, then *"Changed · arrives at next sync. Changed your
   mind? Remove from watch."*

## 4. Remove from watch

1. After a send, double-tap *"Remove from watch"* on the toast, or open a session that's on
   the watch and use *"Remove from watch"*.
2. The confirm sheet lists *"Remove, <day>, <session>"* and *"Confirm and send 1 change"*.
3. After it goes: the session stays in the plan and its status says *"Not on your watch"*.

## 5. Onboarding → the first workout lands

(You → *"Try onboarding"* with sample data.)

1. Welcome: the app's name and line first, then *"Try a session first"* and *"Set up my
   plan"*, *"Sign in"*.
2. Goal (*"Step 1 of 3"*), paces (*"Step 2 of 3"*), result, then the reveal: *"Step 3 of 3 ·
   Your plan · 20 weeks"*, *"London Marathon, Sat 24 Apr"* (heading), *"Phases: Base 6 weeks,
   Build 7 weeks, Peak 4 weeks, Taper 3 weeks"*, *"Predicted now, 3:20"*, *"Target, 3:15"*,
   *"Week 1"* (heading), each day as one element, then *"Send week 1 to my watch"*.
3. Confirm sheet: *"5 changes on your watch"*, one line per session, *"Confirm and send 5
   changes"*.
4. The landing shows *"Sending Monday's run to your watch"*; you should hear *"Sending to
   …"*, then *"Changed · arrives at next sync…"* or *"On your watch…"* as week 1 lands (or
   *"Not sent. …"*), then the success check (*"Done"*) and only then the notification ask: *"Allow notifications"*, *"Not now"*. The
   system's own permission prompt follows *"Allow notifications"*.

## 6. Coach: ask → proposal → confirm

(Coach on. Sample data uses the Coach's stand-in.)

1. Coach: the header *"Coach"*, *"Search the conversation"*, *"Conversation options"*, then
   the thread from the top: day separators are headings (*"Today · 07:00"*), each question and
   reply in order, source chips (*"Sources: Run · Garmin"*), charts as one summary
   (*"Peak heart rate per rep: 164, 165, 166, 168, 168, 172 bpm."*), *"Helpful"* / *"Not
   helpful"* (selected state said), then the prompt chips, the composer (*"Message the Coach"*,
   *"8 left today"*) and *"Send"* (dimmed and said as unavailable when empty).
2. Type a question and send. While it streams you should hear *"Reading your data"* /
   *"Writing"* (a live status), **not** every word as it arrives. When it finishes, the reply
   is announced once: *"The Coach: …"*.
3. A proposal card: *"Proposed change"* (heading), each changed day as one element, *"Review 1
   watch change"*, *"Not now"*. *"Review"* opens the same diff and confirm as flow 1.
4. **Keyboard** (check on both platforms, with and without a screen reader): with the
   composer focused, the header stays at the top, the list shrinks and stays on the newest
   message, the composer sits directly on the keyboard, and the tab bar is hidden. Dismissing
   the keyboard brings the tab bar back. Rotate an Android tablet with the keyboard up: no gap
   and no overlap.

## 7. Paywall → free week

1. From a lock (*"See Plus"* on Trends) or You → *"Get Plus"*. The paywall: *"Close"*, *"Daash
   Plus"*, *"See where your training is heading."* (heading), the preview (*"A preview of the
   prediction trend and race-day projection"*), each Plus item as one element (*"Advanced plan
   styles, More shapes for your training block, Coming in an update"*), *"Always free"*
   (heading) and its items, *"Try Plus free for 7 days"*, *"Restore purchases"*.
2. *"Try Plus free for 7 days"* opens the sheet: *"How your free week works"* (heading),
   *"Today: Everything in Plus unlocks"*, *"Day 5: We remind you the trial is ending"*, *"Day
   7: <store price> a month starts, unless you cancel"*, *"No payment due today. Cancel
   anytime."*, *"Start free week"*, *"Not now"*.
3. *"Start free week"* (the fake store in Expo Go): the sheet and paywall close and focus
   lands back on the feature that was locked, now open.

## After the run

Record per flow and platform: passed, or what was heard instead. Anything structural (an
element that can't be reached, wrong order, a missing name) is a bug against the screen;
copy that reads badly aloud goes to `DECISIONS.md` as an open question.

Maestro flows for the five end-to-end paths are in `mobile/e2e/` (also a device run, with a
development build: `maestro test mobile/e2e/`).
