# Rolling back a deploy

For when a deploy breaks Daash and the quickest fix is the version that worked
before it. It takes about 7 minutes, and every step can be done from the
GitHub app on a phone. Damaged data is a different problem: see
[RECOVERY.md](RECOVERY.md).

## First: is it the deploy?

| What you see | What it is | What to do |
|---|---|---|
| Tool calls failing for many people, starting at a deploy's time | The deploy | Roll back (below) |
| Errors in **Server error lines** that name Daash's code (`File "/app/garmin_mcp/…"`), starting at a deploy | The deploy | Roll back |
| One person failing, everyone else fine | Their account (wrong password, Garmin's 30-minute lock, an ended link) | Don't roll back; help that person |
| `429` or `IP rate limited by Garmin` in the error lines | Garmin slowing the server's sign-ins | Don't roll back; it passes. People already connected keep working |
| Nothing answers, and no deploy happened recently | Fly or the machine | Fly's status page, then **Server error lines** |

Rolling back when Garmin is the problem doesn't help, and it costs another
~40 seconds of downtime.

## Roll back

1. **Find the last good commit.** GitHub → **Actions** → **Deploy hosted
   server**. The newest green run that was started by hand is the deploy that
   broke things. Open the hand-started green run **before** it and copy its
   commit (the 7-character code next to the commit message, for example
   `1db0d43`).
2. **Deploy it.** Same page → **Run workflow** → branch `main` → paste the
   commit into **Commit to deploy** → **Run workflow**.
   - It runs the same tests, takes the same data snapshot and makes the same
     checks as any deploy.
   - If the Garmin sign-in check fails because Garmin is slowing sign-ins (the
     log says `429` or `rate limited`), run it again with **Deploy even if the
     new code can't sign in to Garmin** ticked. Only for that reason.
3. **Check it worked** (about 7 minutes later):
   - the run is green;
   - a question in Claude gets an answer (`How was my last run?`);
   - **Server error lines** shows no new errors;
   - on the PostHog **Daash: Core Health** dashboard, Reliability recovers
     within 15 minutes.

## Then fix main

The machine now runs the old commit, but `main` still holds the bad one. The
next normal deploy would put it back. So, before anyone deploys again, either:

- **revert it:** `git revert <bad commit>` on `main`, push, and run the deploy
  normally; or
- **fix forward:** push a fix to `main` and deploy it normally once its tests
  pass.

Until then, don't run a normal deploy.

## Watch out for

- **Database changes.** Daash adds tables and columns as it starts, and only
  ever adds. Older code ignores what it doesn't know, so rolling back a few
  commits is safe. Before rolling back further than a week, check whether
  anything in between renamed or removed something in the database (search the
  commits for `ALTER TABLE` or `DROP`).
- **Settings.** The rollback deploys with today's settings (sign-up route,
  weekly email, plan builder), not the ones that commit originally had. That's
  usually what you want.
- **Each deploy restarts the one machine:** about 40 seconds when nobody can
  connect. Deploy once, not twice.
