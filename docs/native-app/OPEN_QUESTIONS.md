# Native app: open questions

Things only Bart can answer. The numbers are referenced from
[PLAN.md](PLAN.md). The ones marked **before designs** shape what the designs
should cover.

## Product and scope

1. **The provider adapter layer.** The brief mentions `garmin_mcp/providers/`
   in progress, but it isn't on `main` or on any branch of the repo. Where does
   it live, and when will it land? Until it does, the plan assumes Garmin only,
   with `providers.garmin` in the features answer and `provider` added to
   events once the layer exists. **Before designs**, if COROS should appear in
   the first release.
9. **Is the AI Coach in the first store release, or later?** The plan has it as
   phase 4 behind `coach_enabled`, built on the chat pilot (#22). If it's in v1,
   its privacy wording has to be public before submission, and the chat pilot
   needs merging first. **Before designs.**
11. **Phones only, or iPad and Android tablets too?** The scaffold says phone
   only (`supportsTablet: false`). Tablets add layouts and screenshots to every
   release.
14. **Plus:** rough timing and what's in it? Only so the designs leave room;
   nothing is built for it now. Payments would go through StoreKit and Play
   Billing (both stores require it for digital features), probably via
   RevenueCat.
18. **Should the PWA move to the server-enforced confirm too,** once it exists?
   That would make the rule hold everywhere, not just in the app. The plan
   leaves the PWA's routes alone until you decide.

## Garmin

3. **Garmin's terms for a published app.** Does the official API (Garmin Connect
   Developer Program) access Daash has cover a mobile app in the App Store and
   Google Play, under Daash's name? Is there anything in Garmin's brand or
   attribution rules that conflicts with "no manufacturer logos"? The server
   already writes Garmin's required credit line ("from your Garmin Forerunner
   265"), and the app would show the same. **Before designs.**
6. **Official sign-in only in the app?** The plan never offers the password route
   (the unofficial `garminconnect` sign-in) in the app. Is that right, and do
   accounts on Garmin's official API alone get everything the app shows (full
   history, readiness, HRV)? If not, which screens need an "available with full
   history" state? **Before designs.**
7. **"On your watch" wording.** Garmin tells Daash a workout is on the calendar,
   not when the watch has collected it. The PWA labels this "on your watch"
   today, with "Arrives at its next sync" underneath. Should the native send
   state say "On your watch" (as the brief says) or "On your Garmin calendar,
   arrives at next sync"? **Before designs.**

## Stores and accounts

2. **Bundle ID / application ID.** The scaffold uses `run.daash.app` on both
   platforms (the reverse of daash.run). It can never change once published.
   Confirm or pick another.
8. **Publisher.** Which legal entity publishes the app, and does it have a
   D-U-N-S number? Apple needs one to enrol as an organisation (so the store
   shows a company, not your name). Google needs one for an organisation
   account, which also skips the "12 testers for 14 days" rule that applies to
   new personal accounts.
5. **App Review demo account.** Reviewers can't sign in to a Garmin account. Do
   you want (a) a reviewer-only Daash account that signs in by email code and
   reads demo data, or (b) a real Garmin test account with real training data?
   (a) needs a small server switch for one address. (b) needs a Garmin account
   you're happy to share with Apple and Google.
15. **Minimum OS versions.** The suggestion is iOS 17 or later (`.https`
   callbacks in ASWebAuthenticationSession arrived in 17.4; widgets and Live
   Activities are better there) and Android 10 or later (API 29), or Expo SDK 58's own minimums where they
   are higher. Lower means more phones and more testing.
16. **Devices.** Do you have an iPhone and an Android phone to test on? A Mac
   isn't required, because EAS builds in the cloud, but a physical device is
   needed for push and widgets.
17. **Which domain carries the app links**: `mcp.garmin.daash.run` (where the
   server, the PWA and today's links live) or `daash.run`? The well-known files
   go on whichever it is, and links in chat cards and emails need to point there.

## Sign-in

4. **Sign in with Apple and Google.** The plan starts with Daash's own email code
   and Garmin's approval, and no Apple or Google buttons. Apple's guideline 4.8
   then doesn't ask for Sign in with Apple, because Garmin is the service whose
   data the app shows. Do you want Apple and Google sign-in anyway, for people
   who'd rather not use email? Adding Google means adding Apple too. Both mean
   linking those identities to existing Daash accounts by email.

## Design

10. **Fonts.** The PWA uses Geist and Geist Mono. Should the native app use Geist
    everywhere (the brand look), or SF Pro on iOS and Roboto on Android for body
    text, with Geist for headlines and numbers (platform convention)? The plan
    leans to the second, since the brief asks for native feel. **Before designs.**
12. **Intervals colour in the PWA.** `--intervals` is now in `app.css` (pink,
    `#E07AA8` / `#B23A78`), but the PWA still draws intervals in `--hard`
    orange. Should the PWA switch too, so web and app agree on what an interval
    session looks like?

## Operations

13. **Crash reporting.** Sentry (or similar) is the usual choice, but it's a third
    party receiving crash data, which goes in the privacy labels. Or go without
    at first and rely on the stores' own crash reports?
19. **These docs are public once merged.** `docs/` is the garmin.daash.run site
    (GitHub Pages, no `.nojekyll`), so `docs/native-app/*.md` will be readable
    there, like `billing-readiness.md` already is. Is that fine, or should they
    move somewhere else?
20. **Merge order with the chat pilot (#22).** The features endpoint grows from
    #22's `/app/api/features`. Merging #22 first keeps that one endpoint;
    otherwise the app work adds it and #22 rebases onto it.
