# The Daash app as an installed app (Nov 26, flag `pwa-v2`)

The web app at `/app/` installs to a home screen and sends notifications. There is no native app.
Everything below is behind the PostHog feature flag `pwa-v2`, on for Bart's account first.
`app_opened` and the chat links' `tool=` go to everyone.

## What happens

- **Install sheet.** It shows after a meaningful moment:
  - the plan seen a second time;
  - an open from a chat answer that just sent a workout to the watch;
  - a session moved in the app.

  It never shows on a first open, in the home-screen app, or within 14 days of "Not now". On a computer it shows only when Chrome offers to install.
  - Android and desktop Chrome get our button, which opens Chrome's own prompt.
  - iPhone gets the two taps, drawn (Share, then Add to Home Screen).
- **Notification ask.** Our own words come first, then the phone's question. It shows:
  - after install, on the first open from the home screen;
  - from "Remind me before each session" on Today;
  - from the switch in You.

  It is never asked on load, and on iPhone only from the home-screen app.
- **Notifications.** There are three kinds, each with its own switch in You, all on to start:
  - *Today's session*: at 7am for anyone new; the evening before, 7am or 8am can be chosen; anyone who already chose 6am keeps it.
  - *Plan changes*: how a run counted against the plan once the watch syncs ("Counted your 7.8 km as today's Easy 12 km").
  - *Weekly summary*: Sunday at 6pm, with done against planned km and next week's key session.

  Limits:
  - At most one a day besides plan changes.
  - Nothing from 9pm to 7am, except a heads-up at a time the person chose.
  - A Sunday that already had its heads-up sends the summary with Monday's heads-up instead.

  Each notification opens on its own day. That screen has a link to stop that kind of notification.
- **Chat links.** Every app link names the tool that gave it (`tool=`). Under the flag, one short link a day ends an answer about the plan, a workout sent to the watch, or the week (`get_progress`). Signed out, the link signs in, then opens the screen it was for.

## iPhone limits we hit or worked around

1. **No install button.** Safari has no `beforeinstallprompt`, so the sheet draws the steps. Only Safari can add to the home screen. Chrome, Firefox, Instagram and similar in-app browsers on iPhone are told to open the page in Safari first.
2. **No way to know it was installed** from Safari. The first open from the home screen (`navigator.standalone`) is counted as `pwa_installed` with `via: first_standalone_open`. "Got it" on the sheet is counted as `acknowledged`, not `accepted`.
3. **Notifications only from the home-screen app.** iOS 16.4 or later is needed. Safari in a tab has no `PushManager`, so nothing is asked there. "Remind me" in Safari, if a browser ever offers it, leads to the install sheet instead.
4. **Separate storage.** The home-screen app keeps its own storage, apart from Safari's. Someone signed in in Safari signs in once more in the installed app, as since Nov 4.
5. **Permission is asked once.** If someone says no, iOS doesn't ask again. The setting is then under iPhone Settings → Notifications → Daash. The app says so ("Blocked") and doesn't nag.
6. **Notifications only arrive** while the phone has network. Apple's push service holds them for up to 12 hours (our TTL).
7. **The maskable icon is ignored.** iOS uses `apple-touch-icon.png` (180 × 180).

## Testing it on an iPhone (Bart's account has the flag)

1. In Safari, open the app from a chat answer that sent a workout, or open the app and view Plan twice on a second visit. The "Add Daash to your home screen" sheet appears.
2. Follow it: Share, then Add to Home Screen, then Add.
3. Open Daash from the home screen. You'll see "A nudge from Daash?". Tap Turn on, then Allow.
4. In You → Notifications, check the three switches and the time (7am).
5. Next morning at 7am you get "Today: …". After a run syncs you get "Counted your … km as …". On Sunday at 6pm you get "Your week: …".
6. Tap one. It opens on that day, with "Stop … notifications" at the bottom.

To test on Android: Chrome, a second visit, Plan twice, Install. Desktop Chrome works the same, once Chrome shows its install icon.

## Events to watch in the first week

`app_opened` {source, standalone, tool} · `pwa_install_prompt_shown` {platform, trigger} · `pwa_install_prompt_result` {result} · `pwa_installed` {platform, via} · `push_permission_prompt_shown` {trigger} · `push_permission_result` {result} · `push_sent` {type, result} · `push_failed` {status, type} · `push_clicked` {type} · `push_kind_switched` {type, on, where}

Dashboard: [Daash app: opens, installs, notifications](https://eu.posthog.com/project/127720/dashboard/1155070).
