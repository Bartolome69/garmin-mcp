# garmin-mcp

A local [MCP](https://modelcontextprotocol.io) server that gives Claude read-only
access to your own Garmin Connect data. Single user, runs as a local subprocess
over stdio, no network exposure and no hosting.

It talks to Garmin's unofficial Connect API through the
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library,
because Garmin has no consumer OAuth API. That means it signs in with your real
Garmin username and password.

## Tools

| Tool | What it returns |
| --- | --- |
| `get_daily_summary(date)` | Steps, distance, calories, resting/min/max HR, body battery, stress, intensity minutes |
| `get_sleep_data(date)` | Sleep stages with durations and percentages, sleep score and rating, overnight HRV, resting HR |
| `get_activities(limit, start_date, end_date)` | Recent runs/rides/workouts: distance, duration, pace per km and mile, average and max HR, HR zones, cadence, training effect |
| `get_activity_details(activity_id)` | One activity: per-split distance/pace/HR/cadence, plus full HR time-in-zone |
| `get_connection_status()` | Whether the server is logged in, which account (masked), and the state of the token cache |
| `list_workouts(limit)` | Structured workouts saved in the account |
| `create_workout(name, steps, sport, description)` | Builds a structured workout and adds it to Garmin Connect |
| `schedule_workout(workout_id, date)` | Puts a workout on a date so it syncs to the watch |

The first five are read-only. The two that write are deliberately **additive
only**: they create and schedule, and there is no tool that deletes, overwrites
or edits anything. The worst case is a workout you delete in the Garmin app.

Dates accept `YYYY-MM-DD`, `today`, `yesterday`, or a negative offset like `-7`.

## Creating workouts

`create_workout` takes an ordered list of steps. Each step has a `type`
(`warmup`, `interval`, `recovery`, `rest`, `cooldown` or `repeat`), exactly one
of `duration_seconds` or `distance_meters`, and an optional target — either
`pace` (minutes per km, as `"4:05"` or a range `["4:00","4:10"]`) or `hr`
(`[150, 165]` bpm).

A 15 minute warmup, 5x1km at 4:05 with 90 second recoveries, 10 minute cooldown:

```json
[{"type": "warmup", "duration_seconds": 900},
 {"type": "repeat", "times": 5, "steps": [
     {"type": "interval", "distance_meters": 1000, "pace": "4:05"},
     {"type": "recovery", "duration_seconds": 90}]},
 {"type": "cooldown", "duration_seconds": 600}]
```

A single pace is widened by 5 s/km either side, because Garmin alerts on a range
and an exact target would beep constantly. Repeat groups cannot nest.

Creating a workout only saves it. Schedule it on a date for it to reach the
watch.

## Install

Needs Python 3.10 or newer. The system Python on macOS is 3.9, so this uses
[`uv`](https://docs.astral.sh/uv/) to fetch a modern one:

```bash
cd path/to/garmin-mcp && uv venv --python 3.12 .venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
```

With plain pip and your own Python 3.10+:

```bash
cd path/to/garmin-mcp && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Credentials

The server reads two environment variables and never stores them anywhere:

```bash
export GARMIN_EMAIL="you@example.com"
export GARMIN_PASSWORD="your-garmin-password"
```

There is a `.env.example` to copy if you prefer keeping them in a file — but
note the server does not load `.env` itself, so you would need to source it.

After the first successful login the session token is cached at
`~/.garmin-mcp/tokens.json` (owner-only, `0600`, inside a `0700` directory), and
later runs resume from that instead of signing in again. Override the location
with `GARMIN_MCP_TOKENS` if you want it elsewhere.

### First login, and multi-factor auth

If your Garmin account has multi-factor authentication on, the server cannot
complete the login on its own: it speaks MCP over stdin/stdout, so it has
nowhere to prompt you for a code. Do the first login in a terminal instead:

```bash
cd path/to/garmin-mcp && .venv/bin/python -m garmin_mcp.login
```

It asks for anything not already in the environment (the password is never
echoed), handles the MFA prompt, and caches the token. From then on the server
picks that up. Run it again if the cached session is ever rejected.

## Check it against your real account

The fastest way to find out whether it can actually reach Garmin. It uses the
same code path as the MCP tools and prints what comes back:

```bash
cd path/to/garmin-mcp && .venv/bin/python -m garmin_mcp.check
```

If you have already run the login command below, this needs no password — it
runs off the cached session. Otherwise set the two environment variables first.

## Try it with the MCP Inspector

Before wiring this into Claude Desktop, confirm it works standalone. The
Inspector is a browser UI that speaks MCP to your server — it needs Node, which
you already have.

```bash
cd path/to/garmin-mcp && GARMIN_EMAIL="you@example.com" GARMIN_PASSWORD="your-password" npx @modelcontextprotocol/inspector .venv/bin/python -m garmin_mcp
```

It opens a browser itself, and prints the URL it used:

```
MCP Inspector Web is up and running at:
   http://127.0.0.1:6274?MCP_INSPECTOR_API_TOKEN=ec50549745...
```

Use that exact URL — the token is required, so `localhost:6274` on its own will
not authenticate.

Then, in the Inspector:

1. Press **Connect**. The left pane should show the server as connected.
2. Open the **Tools** tab and press **List Tools**. You should see all five.
3. Run **`get_connection_status`** first — it needs no arguments and tells you
   whether authentication actually worked. Expect `"authenticated": true` and a
   masked account like `y***@example.com`. If it returns an `error` instead,
   fix that before trying anything else.
4. Run **`get_daily_summary`** with `date` empty (defaults to today), then
   **`get_sleep_data`** for `yesterday`.
5. Run **`get_activities`** with `limit` `5`. Copy an `activity_id` from the
   result.
6. Run **`get_activity_details`** with that id to see splits and HR zones.

Watch the **Notifications** pane at the bottom for server-side log lines; that
is where login problems show up.

There is also an offline test that drives the server exactly like a real client
but against fixed sample data, so it needs no credentials and touches no
network:

```bash
cd path/to/garmin-mcp && .venv/bin/python tests/smoke_test.py
```

## Add it to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`. That
file already has other settings in it, so **merge** this `mcpServers` block in
rather than replacing the whole file:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/ABSOLUTE/PATH/TO/garmin-mcp/.venv/bin/python",
      "args": ["-m", "garmin_mcp"],
      "cwd": "/ABSOLUTE/PATH/TO/garmin-mcp",
      "env": {
        "GARMIN_EMAIL": "you@example.com",
        "GARMIN_PASSWORD": "your-garmin-password"
      }
    }
  }
}
```

Restart Claude Desktop fully (quit, don't just close the window). Ask it
something like *"what did my Garmin say about last night's sleep?"*

That config file stores the password in plain text. If you would rather it did
not, run `python -m garmin_mcp.login` once and then drop `GARMIN_PASSWORD` from
the `env` block — the server will run off the cached token alone. You will need
to re-run the login command whenever Garmin expires the session.

## What this never does

- No credential is ever written to disk by this project — only the token the
  Garmin library issues, and only to `~/.garmin-mcp/tokens.json`.
- No tool returns your password or the token contents. `get_connection_status`
  reports a masked address, the cache's path, age and permissions, nothing more.
- Logs go to stderr only, so they never corrupt the MCP stream on stdout, and
  they never include credentials.

## Troubleshooting

**`error` mentioning GARMIN_EMAIL / GARMIN_PASSWORD** — the variables did not
reach the server process. Inside Claude Desktop they must be in the `env` block
above; your shell profile is not read.

**"Garmin is asking for a multi-factor code"** — run
`.venv/bin/python -m garmin_mcp.login` in a terminal, as described above.

**"Garmin is rate-limiting login attempts"** — Garmin throttles repeated
sign-ins. Wait several minutes. Once the token cache exists this stops happening,
since the server resumes instead of logging in.

**A tool returns `"Garmin returned no summary for this day"`** — genuinely no
data: the watch was not worn, or has not synced to Garmin Connect yet.

**The server works in the Inspector but not in Claude Desktop** — almost always
the `command` path. It must be the absolute path to `.venv/bin/python`, not a
bare `python`.

## Layout

```
garmin-mcp/
├── garmin_mcp/
│   ├── __main__.py      # python -m garmin_mcp
│   ├── server.py        # MCP tool definitions
│   ├── session.py       # login, token cache, error translation
│   ├── formatting.py    # Garmin JSON -> compact readable payloads
│   ├── workouts.py      # step descriptions -> Garmin workout JSON
│   ├── login.py         # one-off interactive/MFA login
│   └── check.py         # pull real data, for verifying setup
├── tests/
│   ├── smoke_test.py    # drives the server over stdio, no network
│   ├── fake_server.py   # real server, stubbed Garmin account
│   └── fake_garmin.py   # sample payloads
├── requirements.txt
└── pyproject.toml
```
