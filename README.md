# Daash: free hosted Garmin MCP for Claude and ChatGPT

[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/bartolome69/garmin-mcp)

Daash (https://daash.run) is a free, open-source hosted Garmin Connect
[MCP](https://modelcontextprotocol.io) connector for Claude and ChatGPT.
Nothing to install. It works on the web and in the phone apps. The connector
URL is https://mcp.garmin.daash.run/mcp.

1. Add a custom connector in Claude, or a custom MCP server or app with OAuth in ChatGPT.
2. Paste `https://mcp.garmin.daash.run/mcp`.
3. Sign in to Garmin when asked.

A free Claude account is enough: Settings → Connectors → Add custom connector.
ChatGPT needs a paid plan (Plus, Pro, Business, Enterprise or Edu) and
developer mode. On the web, open Settings → Apps, or Settings → Connectors →
Advanced, and switch developer mode on. On a work or team account, an
administrator has to turn that on. ChatGPT's free plan cannot add connectors.

It reads your activities, sleep, HRV and recovery, and it builds structured
workouts and training plans that go onto your Garmin calendar and sync to
your watch.

The hosted server holds your Garmin session, encrypted.
[The privacy policy](https://garmin.daash.run/privacy/) sets out what is kept.
Not affiliated with Garmin.

```
You:    My last three runs are all at the same effort. Give me something harder
        for Thursday, based on what my recent paces actually support.

Claude: [reads your activities and splits, then proposes]

        Thursday Threshold (running, about 52m 55s)
          warmup: 15m
        5 x
            interval: 1.00 km @ 4:00/km-4:10/km
            recovery: 1m 30s
          cooldown: 10m

        Create this and put it on Thursday?
```

## Optional: run it on your own machine

This is the local alternative to the hosted connector above. It runs as a
subprocess on your computer, with your Garmin session staying on that machine.
It needs macOS and a Terminal window, and only a client that can start a local
process can use it. ChatGPT can't run this local stdio install. For ChatGPT,
use the hosted URL at the top of this page: https://mcp.garmin.daash.run/mcp.

macOS 10.15 (Catalina) or newer, with [Claude Desktop](https://claude.ai/download) already
installed:

```bash
curl -fsSL https://raw.githubusercontent.com/Bartolome69/garmin-mcp/main/scripts/bootstrap.sh | bash
```

That fetches the code to `~/garmin-mcp`, installs a modern Python via
[uv](https://docs.astral.sh/uv/), signs you in to Garmin, and registers the
server with Claude Desktop. It's the only command most people need. Read it
first if you'd rather —
[`scripts/bootstrap.sh`](scripts/bootstrap.sh) is short.

Then quit Claude Desktop completely (⌘Q) and reopen it.

<details>
<summary>Manual install, or on Linux</summary>

```bash
git clone https://github.com/Bartolome69/garmin-mcp.git
cd garmin-mcp
./scripts/setup.sh          # venv + dependencies
./scripts/login.sh          # sign in to Garmin once, caches the session
```

Then register it with your MCP client. For Claude Desktop on macOS,
`./scripts/install-claude-desktop.sh` does it (with the app quit). For anything
else, copy `.mcp.json.example`, fill in the absolute paths, and point your client
at `python -m garmin_mcp` over stdio.

</details>

## Tools

| Tool | What it returns |
| --- | --- |
| `get_activities(limit, start_date, end_date)` | Runs, rides and workouts: distance, duration, pace per km and mile, average and max HR, HR zones, cadence, training effect, plus running dynamics and power when the watch records them |
| `get_activity_details(activity_id)` | One activity in detail: per-split distance, pace, HR, cadence, running dynamics and power, full heart-rate time-in-zone, and what the recording says inside each lap: HR drift from first third to last at the paces run, plus per-km splits when the watch did not lap by kilometre |
| `get_readiness(date)` | Today's recovery picture in one call: Garmin's readiness score and its factors, training status, acute against chronic load, four-week load focus, overnight HRV against baseline, body battery, stress, resting HR and sleep |
| `get_fitness()` | Fitness markers: VO2 max, predicted race times from 5k to marathon, lactate threshold pace and HR, endurance score, hill score and running tolerance, each classified where Garmin classifies them |
| `get_daily_summary(date)` | Steps, distance, calories, resting/min/max HR, body battery, stress, intensity minutes |
| `get_sleep_data(date)` | Sleep stages with durations and percentages, sleep score, overnight HRV, resting HR |
| `list_workouts(limit)` | Structured workouts saved in the account |
| `create_workout(name, steps, sport, description)` | Builds a structured workout and adds it to Garmin Connect |
| `update_workout(workout_id, name, steps, sport, description)` | Changes a workout in place. Same id, so dates it is scheduled on stay scheduled and the watch gets the new version at the next sync |
| `schedule_workout(workout_id, date)` | Puts a workout on a date, which is what syncs it to the watch |
| `unschedule_workout(date, schedule_id)` | Takes a workout off a day. Reversible — the workout itself is kept |
| `delete_workout(workout_id, confirm)` | Deletes a workout. The first call only reads its name back; removing it needs a second call quoting that name |
| `create_plan(goal, sessions, label)` | Builds and schedules a whole training block in one call. Every session is validated first, so a mistake fails the call without leaving half a plan behind. Each workout is named with a short plan code, "Threshold 5x1k · HM", which is how the plan is found again; nothing about it is stored outside Garmin |
| `get_plan(label)` | The plan week by week, each session done, done on another day, missed, today or ahead, with completion so far, the next session, and what slipped in the last week. In Claude, ChatGPT and VS Code it also opens as an interactive plan view in the chat |
| `remove_plan(label, confirm)` | Takes the rest of a plan off the calendar and out of the library. Past sessions stay. The first call only reports what would go |
| `get_plan_chart(weeks_back, weeks_forward)` | Draws the plan as an image for the conversation: each day's sessions beside what was planned |
| `get_progress(weeks)` | Which planned sessions actually got done, week by week. Counts a session if it happened within a day either side of its scheduled day |
| `get_profile()` | VO2 max and personal records — 5k, 10k, half, marathon and the rest |
| `get_connection_status()` | Whether the server is signed in, which account (masked), and the state of the token cache |

Dates accept `YYYY-MM-DD`, `today`, `yesterday`, `tomorrow`, or a signed offset
like `-7` or `+3`.

## Writing workouts

`create_workout` takes an ordered list of steps. Each has a `type` (`warmup`,
`interval`, `recovery`, `rest`, `cooldown` or `repeat`), exactly one of
`duration_seconds` or `distance_meters`, and an optional target — either `pace`
(minutes per km, as `"4:05"` or a range `["4:00","4:10"]`) or `hr` (`[150, 165]`).

15 minute warmup, 5×1km at 4:05 with 90 second recoveries, 10 minute cooldown:

```json
[{"type": "warmup", "duration_seconds": 900},
 {"type": "repeat", "times": 5, "steps": [
     {"type": "interval", "distance_meters": 1000, "pace": "4:05"},
     {"type": "recovery", "duration_seconds": 90}]},
 {"type": "cooldown", "duration_seconds": 600}]
```

A single pace is widened by 5 s/km either side, because Garmin alerts on a range
and an exact target beeps constantly. Repeat groups don't nest. Creating a
workout only saves it — schedule it on a date for it to reach the watch.

## What it can and can't do to your account

Reading is unrestricted. Writing is confined to the **workout library**: create a
workout, schedule it, unschedule it, delete it. Nothing reaches your training
history — no tool deletes or edits a recorded activity, so a run you did cannot
be lost here however wrong a tool call goes.

`delete_workout` is gated rather than trusted to a docstring. The first call
never deletes: it reads the workout back and returns its name, and only a second
call passing that name as `confirm` removes it. A model cannot destroy a workout
it has not first named.

Your password is read from the environment, sent straight to Garmin, and never
written to disk. Only the session token Garmin issues is cached, at
`~/.garmin-mcp/tokens.json`, written `0600` inside a `0700` directory. No tool
returns the password or the token — `get_connection_status` reports a masked
address and the cache's age and permissions, nothing more. Logs go to stderr, so
they never corrupt the MCP stream on stdout.

After the first sign-in the server runs off the cached token. Set
`GARMIN_EMAIL` and `GARMIN_PASSWORD` in the server's environment if you want it
to re-authenticate unattended when that token eventually expires; leave
`GARMIN_PASSWORD` out and you'll re-run `scripts/login.sh` instead.

## If it doesn't work

Start here — it walks from the interpreter to a live Garmin call and names the
first broken link:

```bash
./scripts/doctor.sh
```


**"Garmin is rate-limiting logins from this IP address (429)"** — the most common
failure, and it isn't your password: Garmin blocks by network address before it
checks credentials. Office wifi, university networks and VPNs get hit hardest.
Sign in once over a phone hotspot; afterwards the cached session is used instead.

**"Garmin is asking for a multi-factor code"** — the server can't prompt over
stdio, so run `./scripts/login.sh` in a terminal once. It handles the code and
caches the session.

**Claude can't see the tools** — Claude Desktop loads its config at launch and
writes its own copy back when it closes, so a change made while it's running
disappears. Quit it fully, run `./scripts/install-claude-desktop.sh`, reopen.

**Install fails mentioning Rust, OpenSSL or a compiler** — macOS is too old.
The hard floor is **10.15 (Catalina)**, set by the Python interpreter itself:
uv's Intel build is compiled with `minos 10.15` and will not launch below it, so
no amount of package pinning helps. The installers pass `--only-binary :all:` so
this fails fast rather than becoming a doomed source build, and retry against
`constraints-legacy.txt` in case a package has simply dropped a wheel.

**No sleep data** — the watch wasn't worn overnight, or hasn't synced. Sleep,
HRV and overnight body battery only exist if you sleep in it.

## Training skills

[`skills/`](skills/) holds training methodology that builds on the tools — how
to read the data and what to prescribe from it. The first is a half marathon
skill in Pete Pfitzinger's style.

## Development

```bash
.venv/bin/python tests/smoke_test.py
```

Drives the server over real stdio like an MCP client would, against a stubbed
Garmin account — no network, no credentials. Covers every tool's response shape,
workout construction, bad input, and the no-credentials startup path.

```bash
.venv/bin/python -m garmin_mcp.check
```

The same code path against your real account, printing what comes back. Useful
for confirming a setup end to end.

```bash
.venv/bin/python tests/http_test.py
```

The HTTP transport, as Claude drives it: discovery, client registration,
Google sign-in (stubbed), consent, token exchange and rotation, and tool calls
with the token.

## Other MCP clients

Nothing in the local install is Claude-specific: it speaks MCP over stdio, so
any client that launches a local server will run it — Claude Code, Cursor,
VS Code, Zed, Windsurf. Copy `.mcp.json.example`, fill in absolute paths,
point the client at `python -m garmin_mcp`.

**ChatGPT can't run this local stdio install.** Its connectors take a public
HTTPS URL, because ChatGPT executes on OpenAI's servers and cannot start a
process on your machine. ChatGPT users should use the hosted connector:
https://mcp.garmin.daash.run/mcp. A paid ChatGPT plan and developer mode are
required; see the top of this page.

## Caveats

Not affiliated with Garmin. It uses the same private API the Garmin Connect
website does, via
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect), because
Garmin publishes no consumer OAuth API. That API can change without notice and
take this with it.

MIT licensed. Built with [Claude Code](https://claude.com/claude-code).
