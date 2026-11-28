"""The in-app chat pilot (chat_pilot.py), against the demo Garmin account and a scripted model.

What has to hold:
- the flag: only a person in CHAT_PILOT_USER_IDS gets chat; everyone else gets
  the same 404 an unknown app route gives, and with the variable unset nobody has it;
- confirm before write: a change the model asks for doesn't run until Confirm,
  Decline never runs it, and Confirm runs that exact stored call exactly once;
- the guards: the daily cap, the tool-call cap per message, a stream that dies saved as partial.

No model is called: chat_pilot.make_provider is replaced by a script.

    .venv/bin/python tests/chat_pilot_test.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "garmin-chat-test"
SCRATCH.mkdir(parents=True, exist_ok=True)

from cryptography.fernet import Fernet  # noqa: E402

os.environ["GARMIN_MCP_DB"] = str(SCRATCH / f"test-{secrets.token_hex(6)}.sqlite3")
os.environ["GARMIN_MCP_SECRET"] = Fernet.generate_key().decode()
os.environ["GARMIN_MCP_OAUTH"] = "1"
os.environ["GARMIN_MCP_BASE_URL"] = "http://localhost"
os.environ["GARMIN_MCP_POSTHOG_KEY"] = "phc_test_key"
os.environ["CHAT_API_KEY"] = "test-key"
os.environ.pop("CHAT_PILOT_USER_IDS", None)
for name in ("CHAT_PROVIDER", "CHAT_MODEL", "CHAT_DAILY_MESSAGE_CAP", "CHAT_MAX_TOOL_CALLS"):
    os.environ.pop(name, None)

from garmin_mcp import session as session_mod  # noqa: E402
from tests.demo_garmin import DemoGarmin  # noqa: E402

DEMO = DemoGarmin()
session_mod.build_client = lambda **_: DEMO

import garmin_mcp.hosted as hosted  # noqa: E402
from garmin_mcp import analytics, chat_pilot, server, store  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

SENT: list[dict] = []
analytics._post = SENT.append
analytics._fetch_flags = lambda who: {}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def posted(event: str) -> list[dict]:
    time.sleep(0.3)
    return [e for e in SENT if e["event"] == event]


# -- a scripted model ----------------------------------------------------------------

class Scripted:
    """Answers each model call from a list of steps: some text, then the tools it asks for."""

    def __init__(self, steps: list[dict]) -> None:
        self.steps = list(steps)
        self.results: list[list[tuple[str, str, bool]]] = []
        self.system = ""
        self.history: list[dict] = []

    async def call(self, client, on_text):
        step = self.steps.pop(0) if self.steps else {"text": "That's all."}
        if step.get("raise"):
            await on_text(step.get("text", ""))
            raise chat_pilot.ProviderError("provider", "stream died")
        out = chat_pilot.Call()
        out.text = step.get("text", "")
        await on_text(out.text)
        out.tools = [{"id": f"t{i}", "name": n, "args": a, "invalid": False} for i, (n, a) in enumerate(step.get("tools", []))]
        out.input_tokens, out.output_tokens, out.stop = 1000, 50, "tool_use" if out.tools else "end_turn"
        return out

    def add_results(self, call, results):
        self.results.append(results)


SCRIPT: dict[str, Scripted] = {}


def use_script(steps: list[dict]) -> Scripted:
    scripted = Scripted(steps)

    def make(cfg, system, tools, history):
        scripted.system, scripted.history, scripted.tools = system, history, tools
        return scripted

    chat_pilot.make_provider = make
    return scripted


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if not lines or not lines[0].startswith("event:"):
            continue
        out.append((lines[0][6:].strip(), json.loads(lines[1][5:].strip())))
    return out


# -- writes, counted -------------------------------------------------------------------
# Stand-ins with the real signatures, so _check_args holds them to the same arguments.

CALLS: dict[str, list[dict]] = {"schedule_workout": [], "delete_workout": [], "get_activities": []}
_real_get_activities = server.get_activities


async def fake_schedule_workout(workout_id: int | str, date: str) -> dict:
    CALLS["schedule_workout"].append({"workout_id": workout_id, "date": date})
    return {"workout_id": int(workout_id), "date": date, "schedule_id": 990001}


async def fake_delete_workout(workout_id: int | str, confirm: str = "") -> dict:
    if confirm != "Old tempo":
        return {"workout_id": int(workout_id), "name": "Old tempo", "confirmation_required": True, "note": "Nothing deleted."}
    CALLS["delete_workout"].append({"workout_id": workout_id, "confirm": confirm})
    return {"deleted": int(workout_id), "name": "Old tempo", "note": "Permanently removed."}


async def counted_get_activities(limit: int = 10, start_date: str | None = None, end_date: str | None = None) -> dict:
    CALLS["get_activities"].append({"limit": limit})
    return await _real_get_activities(limit=limit, start_date=start_date, end_date=end_date)


MAKE_PROVIDER = chat_pilot.make_provider
server.schedule_workout = fake_schedule_workout
server.delete_workout = fake_delete_workout


def adapter_checks() -> None:
    """The two real adapters read their provider's stream: text, a tool call, usage."""
    import asyncio

    httpx = chat_pilot.httpx

    def sse(*events: tuple[str | None, dict | str]) -> bytes:
        out = ""
        for name, data in events:
            out += (f"event: {name}\n" if name else "") + "data: " + (data if isinstance(data, str) else json.dumps(data)) + "\n\n"
        return out.encode()

    anthropic_stream = sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 900, "cache_read_input_tokens": 4000}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}}),
        ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Let me look."}}),
        ("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tu_1", "name": "get_plan", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"label"'}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": ': "HM"}'}}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 42}}),
        ("message_stop", {"type": "message_stop"}))
    openai_stream = sse(
        (None, {"choices": [{"delta": {"content": "Checking"}}]}),
        (None, {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "get_readiness", "arguments": '{"da'}}]}}]}),
        (None, {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'te": "today"}'}}]}, "finish_reason": "tool_calls"}]}),
        (None, {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 30}}),
        (None, "[DONE]"))
    seen: list[dict] = []

    def handler(request):
        seen.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        body = anthropic_stream if "anthropic" in str(request.url) else openai_stream
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async def run(provider_name: str):
        os.environ["CHAT_PROVIDER"] = provider_name
        cfg = chat_pilot.env()
        os.environ.pop("CHAT_PROVIDER")
        tools = [{"name": "get_plan", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        provider = chat_pilot.make_provider(cfg, "system", tools, [{"role": "user", "text": "hi"}])
        said: list[str] = []

        async def on_text(t):
            said.append(t)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            call = await provider.call(client, on_text)
        provider.add_results(call, [(call.tools[0]["id"], '{"ok":true}', False)])
        return cfg, call, "".join(said), provider

    real = chat_pilot.make_provider
    chat_pilot.make_provider = MAKE_PROVIDER
    try:
        cfg, call, said, provider = asyncio.run(run("anthropic"))
        check("Claude: text streams, the tool call and its arguments are read",
              said == "Let me look." and call.tools == [{"id": "tu_1", "name": "get_plan", "args": {"label": "HM"}, "invalid": False}],
              str(call.tools))
        check("Claude: usage, cache reads and the stop reason are read",
              (call.input_tokens, call.cache_read, call.output_tokens, call.stop) == (900, 4000, 42, "tool_use"))
        check("Claude: the assistant turn goes back with its thinking block intact, then the tool result",
              provider.messages[-2]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
              and provider.messages[-1]["content"][0]["tool_use_id"] == "tu_1")
        sent = seen[-1]
        check("Claude: the default is Haiku 5.5 at low effort, the key in x-api-key, no temperature",
              cfg["model"] == "claude-haiku-5-5" and sent["body"]["model"] == "claude-haiku-5-5"
              and sent["body"]["output_config"] == {"effort": "low"} and sent["headers"]["x-api-key"] == "test-key"
              and "temperature" not in sent["body"] and sent["body"]["max_tokens"] == 1024)
        cfg, call, said, provider = asyncio.run(run("openai"))
        check("OpenAI: text, the tool call pieced together, and usage",
              said == "Checking" and call.tools[0]["args"] == {"date": "today"} and call.input_tokens == 1200
              and call.output_tokens == 30 and cfg["model"] == "gpt-4o-mini", str(call.tools))
        check("OpenAI: the tool result goes back as a tool message",
              provider.messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'})
        cfg, call, said, provider = asyncio.run(run("gemini"))
        check("Gemini: Google's OpenAI-compatible endpoint and Flash by default",
              "generativelanguage.googleapis.com" in seen[-1]["url"] and cfg["model"] == "gemini-2.5-flash")
        check("cost estimate: Haiku 5.5 at list price, cache reads at a tenth",
              chat_pilot.estimate_cost(chat_pilot.env(), 1_000_000, 100_000, cache_read=1_000_000) == 0.16)
    finally:
        chat_pilot.make_provider = real


def context_checks() -> None:
    """What the model is told before it answers (Nov 28): a chat said a "Half check" had no target
    and missed the goal race, because get_plan's goal and races sat after its weeks and were cut
    off, and fitness was never read. Now the system prompt carries them, and a cut-short plan
    loses far-off weeks, never the goal."""
    today = date(2026, 11, 28)
    week = lambda n, starts, sessions: {"week": n, "starts": starts, "planned_km": 60, "run_km": 40, "sessions": sessions}
    plan = {
        "for_the_assistant": "The user is looking at this as an interactive plan card.",
        "app": {"url": "https://example.test/app/#/plan?login=SECRET", "note": "link"}, "_card": "https://example.test/card/seen?u=x",
        "weeks": [week(n, (date(2026, 11, 2) + timedelta(days=7 * (n - 1))).isoformat(),
                       [{"date": (date(2026, 11, 2) + timedelta(days=7 * (n - 1) + d)).isoformat(), "day": "Mon",
                         "name": "Easy run " + "x" * 200, "type": "easy", "status": "ahead", "planned_km": 10}
                        for d in range(7)]) for n in range(1, 21)],
        "phases": [{"label": "Base", "from_week": 1, "to_week": 9}, {"label": "Build", "from_week": 10, "to_week": 20}],
        "starts": "2026-11-02", "ends": "2027-03-21", "sessions_total": 137, "completed": 19, "missed": 5, "remaining": 113,
        "completion_percent": 79,
        "current_plan": {"label": "MAR", "starts": "2026-11-07", "ends": "2027-04-24", "weeks_total": 25, "week": 4,
                         "goal": "Spring Marathon, sub-3:00", "name": "Spring build", "method": "Base then marathon-specific"},
        "this_week": {"week": 4, "unit": "km", "done": 43, "to_go": 18, "planned": 63},
        "races": [{"name": "Half check", "date": "2026-11-29", "tier": "key", "target": "1:25", "distance_km": 21.1},
                  {"name": "Old 10k", "date": "2026-11-01", "tier": "tune", "distance_km": 10},
                  *[{"name": f"Parkrun {i}", "date": f"2027-01-{10 + i:02d}", "tier": "tune", "distance_km": 5} for i in range(8)],
                  {"name": "Spring Marathon", "date": "2027-04-24", "tier": "goal", "target": "sub-3:00", "distance_km": 42.2}],
        "checks": [{"rule": "b_races_close", "date": "2026-12-05", "text": "Two halves six days apart."},
                   {"rule": "old", "date": "2026-11-01", "text": "A check already behind us."}],
    }
    lines = "\n".join(chat_pilot._plan_lines(plan, today))
    check("the prompt names the plan, the week of the block and its phase",
          "Plan: Spring build" in lines and "week 4 of 25" in lines and "Base phase" in lines, lines[:300])
    check("and the goal and method", "Goal: Spring Marathon, sub-3:00." in lines and "Method: Base then marathon-specific." in lines)
    check("sessions so far are done against due, with the whole plan's count beside them",
          "19 done, 5 missed, 113 still to come (137 in all)" in lines and "79" not in lines)
    check("races ahead carry their targets; past ones are left out",
          "Half check (key, 21.1 km, target 1:25)" in lines and "Old 10k" not in lines)
    check("the goal race is always there, even past the first six",
          "Spring Marathon (goal, 42.2 km, target sub-3:00)" in lines and "and 3 more" in lines, lines)
    check("the plan's own checks still ahead are passed on, not the ones behind",
          "Two halves six days apart." in lines and "already behind" not in lines)

    fit = {"race_predictions": {"5k": {"time": "19m 10s"}, "half_marathon": {"time": "1h 29m 30s"}},
           "race_predictions_as_of": "2026-11-27", "lactate_threshold": {"heart_rate_bpm": 168, "pace_per_km": "4:05 /km"},
           "vo2_max": {"value": 57}, "zones": {"heart_rate": {"max_hr": 188, "resting_hr": 45,
                                                              "zones": [{"zone": 1, "bpm": [95, 130]}, {"zone": 2, "bpm": [131, 150]}]}}}
    said = chat_pilot._fitness_lines(fit)[0]
    check("fitness markers go in the prompt: predictions, threshold, VO2 max, zones",
          "5k 19m 10s" in said and "half 1h 29m 30s" in said and "168 bpm at 4:05/km" in said
          and "VO2 max 57" in said and "Z2 131-150" in said and "max 188, resting 45" in said, said)
    check("fitness that couldn't be read says so", "not available" in chat_pilot._fitness_lines({"error": "Timeout"})[0])

    text = chat_pilot._compact("get_plan", plan, 3000, today)
    check("a plan cut short keeps the goal and the races' targets: they come before the weeks",
          '"goal":"Spring Marathon, sub-3:00"' in text and '"target":"1:25"' in text and text.index('"current_plan"') < 30, text[:200])
    check("the app's sign-in link and the card's beacon never reach the model", "login=" not in text and "_card" not in text
          and "SECRET" not in chat_pilot._compact("get_plan", plan, 100000, today))
    check("and the card's own note to the assistant is left out", "interactive plan card" not in chat_pilot._compact("get_plan", plan, 100000, today))
    full = json.loads(chat_pilot._compact("get_plan", plan, 100000, today))
    check("the card's completion figure is named for what it is",
          full.get("done_percent_of_sessions_due_so_far") == 79 and "completion_percent" not in full)
    check("the fitness and trend reads are offered", {"get_fitness", "get_fitness_trends", "get_recovery_trends",
                                                      "get_progress", "get_shoes"} <= set(chat_pilot.READS))


def person(client: TestClient, email: str) -> tuple[str, dict]:
    user_token = store.save_user(session_mod.mask_email(email), '{"di_token": "x"}',
                                 email_hash=store.email_fingerprint(email), email=email)
    token = secrets.token_urlsafe(24)
    store.save_oauth_token(token, kind="access", client_id="daash-app", subject=user_token, scopes="",
                           resource=None, expires_at=int(time.time()) + 3600)
    return user_token, {"Authorization": f"Bearer {token}"}


def main() -> int:
    adapter_checks()
    context_checks()
    client = TestClient(hosted.build_app(), base_url="http://localhost")
    bart_token, bart = person(client, "bart@example.com")
    other_token, other = person(client, "someone@example.com")
    third_token, third = person(client, "third@example.com")
    bart_id = analytics.distinct_id(store.email_fingerprint("bart@example.com"))
    future = (date.today() + timedelta(days=60)).isoformat()

    # -- the flag ---------------------------------------------------------------------
    check("signed out, chat says 401 like every app route", client.get("/app/api/chat").status_code == 401)
    unknown = client.get("/app/api/no-such-thing", headers=bart)
    gated = [("GET", "/app/api/chat", None), ("POST", "/app/api/chat/send", {"text": "hi"}),
             ("POST", "/app/api/chat/action", {"id": 1, "decision": "confirm"}),
             ("POST", "/app/api/chat/feedback", {"id": 1, "value": "up"}), ("POST", "/app/api/chat/new", {})]

    def all_hidden(headers: dict) -> bool:
        for method, path, body in gated:
            r = client.request(method, path, headers=headers, json=body)
            if r.status_code != unknown.status_code or r.json() != unknown.json():
                print("   ", method, path, r.status_code, r.text[:120])
                return False
        return True

    check("with CHAT_PILOT_USER_IDS unset, nobody has chat: the same 404 as an unknown route",
          unknown.status_code == 404 and all_hidden(bart))
    check("and features says nothing", client.get("/app/api/features", headers=bart).json() == {})

    os.environ["CHAT_PILOT_USER_IDS"] = f"{bart_id}, {store.email_fingerprint('third@example.com')}"
    check("the allowlisted person's app is told chat is on",
          client.get("/app/api/features", headers=bart).json() == {"chat": True})
    check("by email fingerprint too", client.get("/app/api/features", headers=third).json() == {"chat": True})
    check("anyone else's features say nothing", client.get("/app/api/features", headers=other).json() == {})
    check("and every chat route gives them the plain 404", all_hidden(other))
    use_script([{"text": "never"}])
    r = client.post("/app/api/chat/send", headers=other, json={"text": "hi"})
    check("a non-allowlisted send never reaches the model", r.status_code == 404 and not SENT_KINDS("chat_response"))
    opened = client.get("/app/api/chat", headers=bart)
    check("the allowlisted person opens the thread, with the starter chips",
          opened.status_code == 200 and opened.json()["messages"] == [] and len(opened.json()["starters"]) >= 4,
          opened.text[:200])
    check("chat_opened is counted", len(posted("chat_opened")) == 1)

    # -- a plain read and the system prompt ----------------------------------------------
    server.get_activities = counted_get_activities
    scripted = use_script([{"text": "", "tools": [("get_activities", {"limit": 5})]},
                           {"text": "Four runs, all easy. A solid week."}])
    r = client.post("/app/api/chat/send", headers=bart, json={"text": "How did this week go?", "starter": True})
    got = events(r.text)
    kinds = [k for k, _ in got]
    check("the answer streams as server-sent events: start, the tool, text, done",
          r.headers["content-type"].startswith("text/event-stream") and kinds[0] == "start" and "tool" in kinds
          and "delta" in kinds and kinds[-1] == "done", str(kinds))
    check("the read ran through the same tool function", CALLS["get_activities"][-1:] == [{"limit": 5}])
    check("the system prompt has today's date, the plan week and readiness, not raw JSON",
          "Europe/London" in scripted.system and "This week" in scripted.system and "Readiness today" in scripted.system
          and "Fitness" in scripted.system
          and '"weeks"' not in scripted.system, scripted.system[:400])
    check("the model is offered the MCP tools' own descriptions, writes marked as proposals",
          any(t["name"] == "create_workout" and "taps Confirm" in t["description"] for t in scripted.tools)
          and any(t["name"] == "get_plan" for t in scripted.tools))
    sent = posted("chat_message_sent")
    check("chat_message_sent says a starter was used and the first tool the model called",
          sent and sent[-1]["properties"].get("starter_used") is True
          and sent[-1]["properties"].get("matched_tool") == "get_activities", str(sent[-1:]))
    resp = posted("chat_response")
    check("chat_response carries latency, tokens, cost and model",
          resp and all(k in resp[-1]["properties"] for k in ("latency_ms", "input_tokens", "output_tokens",
                                                             "est_cost_usd", "model")), str(resp[-1:]))
    check("chat_tool_called says which tool and whether it worked",
          any(e["properties"].get("tool") == "get_activities" and e["properties"].get("ok") is True
              for e in posted("chat_tool_called")))
    thread = client.get("/app/api/chat", headers=bart).json()["messages"]
    check("the thread keeps the message and the answer", [m["role"] for m in thread] == ["user", "assistant"]
          and thread[1]["text"] == "Four runs, all easy. A solid week.", str(thread))
    row = store.chat_message(thread[1]["id"], store.email_fingerprint("bart@example.com"))
    check("tokens, model and estimated cost are kept with the answer",
          row["input_tokens"] == 2000 and row["output_tokens"] == 100 and row["model"] == "claude-haiku-5-5"
          and row["est_cost_usd"] is not None, str(row))

    # -- confirm before write ----------------------------------------------------------
    use_script([{"text": "I'll put it on that day.", "tools": [("schedule_workout", {"workout_id": 555002, "date": future})]},
                {"text": "I've proposed it. Tap Confirm to send it to your watch."}])
    r = client.post("/app/api/chat/send", headers=bart, json={"text": "Put the tempo on that day"})
    pending = [d for k, d in events(r.text) if k == "pending"]
    check("a write comes back as a proposed change with a plain-English summary",
          len(pending) == 1 and "goes to your watch" in pending[0]["text"] and pending[0]["status"] == "pending",
          str(pending))
    check("and nothing ran on Garmin", CALLS["schedule_workout"] == [])
    first = pending[0]["id"]
    r = client.post("/app/api/chat/action", headers=bart, json={"id": first, "decision": "decline"})
    check("Decline cancels it", r.status_code == 200 and r.json()["status"] == "declined", r.text)
    r = client.post("/app/api/chat/action", headers=bart, json={"id": first, "decision": "confirm"})
    check("a declined change can't be confirmed afterwards", r.status_code == 409, r.text)
    check("a declined change never runs", CALLS["schedule_workout"] == [])
    check("chat_write_declined is counted", len(posted("chat_write_declined")) == 1)

    use_script([{"text": "", "tools": [("schedule_workout", {"workout_id": 555002, "date": future})]},
                {"text": "Proposed."}])
    second = [d for k, d in events(client.post("/app/api/chat/send", headers=bart, json={"text": "Again"}).text)
              if k == "pending"][0]["id"]
    r = client.post("/app/api/chat/action", headers=third, json={"id": second, "decision": "confirm"})
    check("someone else can't confirm it, even with chat", r.status_code == 404 and CALLS["schedule_workout"] == [], r.text)
    r = client.post("/app/api/chat/action", headers=bart, json={"id": second, "decision": "confirm"})
    check("Confirm runs it", r.status_code == 200 and r.json()["status"] == "done" and r.json()["refresh"] is True, r.text)
    check("exactly the stored call, once", CALLS["schedule_workout"] == [{"workout_id": 555002, "date": future}],
          str(CALLS["schedule_workout"]))
    r = client.post("/app/api/chat/action", headers=bart, json={"id": second, "decision": "confirm"})
    check("a second Confirm doesn't run it again", r.status_code == 409 and len(CALLS["schedule_workout"]) == 1, r.text)
    r = client.post("/app/api/chat/action", headers=bart, json={"id": second, "decision": "decline"})
    check("nor can it be declined once done", r.status_code == 409, r.text)
    check("chat_write_confirmed is counted", any(e["properties"].get("tool") == "schedule_workout"
                                                 for e in posted("chat_write_confirmed")))
    after = client.get("/app/api/chat", headers=bart).json()["messages"]
    check("the outcome goes in the thread, so the model knows next time",
          after[-1]["role"] == "assistant" and after[-1]["text"].startswith("Done."), str(after[-1]))

    # A proposal more than a day old is refused, not run.
    use_script([{"text": "", "tools": [("schedule_workout", {"workout_id": 555002, "date": future})]}, {"text": "ok"}])
    old = [d for k, d in events(client.post("/app/api/chat/send", headers=bart, json={"text": "Once more"}).text)
           if k == "pending"][0]["id"]
    with store._connect() as conn:
        conn.execute("UPDATE chat_messages SET created_at = created_at - 90000 WHERE id = ?", (old,))
    r = client.post("/app/api/chat/action", headers=bart, json={"id": old, "decision": "confirm"})
    check("a proposal over a day old expires instead of running", r.status_code == 409
          and len(CALLS["schedule_workout"]) == 1, r.text)

    # delete_workout already asks first in MCP: the preview read runs, the delete waits for Confirm.
    use_script([{"text": "", "tools": [("delete_workout", {"workout_id": 555099})]}, {"text": "Proposed."}])
    card = [d for k, d in events(client.post("/app/api/chat/send", headers=bart, json={"text": "Delete old tempo"}).text)
            if k == "pending"][0]
    check("a delete is proposed with the workout's name, and nothing is deleted",
          "Old tempo" in card["text"] and CALLS["delete_workout"] == [], str(card))
    client.post("/app/api/chat/action", headers=bart, json={"id": card["id"], "decision": "confirm"})
    check("Confirm deletes it once, with the confirm Garmin's preview asked for",
          CALLS["delete_workout"] == [{"workout_id": 555099, "confirm": "Old tempo"}], str(CALLS["delete_workout"]))

    # -- the guards -----------------------------------------------------------------------
    CALLS["get_activities"].clear()
    # Limits 11 to 20: the system prompt's own read of recent runs asks for 6.
    scripted = use_script([{"text": "", "tools": [("get_activities", {"limit": n}) for n in range(11, 21)]},
                           {"text": "Done looking."}])
    client.post("/app/api/chat/send", headers=bart, json={"text": "Look at everything"})
    model_calls = [c for c in CALLS["get_activities"] if c["limit"] > 10]
    check("no more than 8 tool calls run for one message", len(model_calls) == 8, str(len(model_calls)))
    check("the rest are answered with the limit, not run",
          sum(1 for _, text, err in scripted.results[0] if err and "limit" in text) == 2, str(scripted.results[0][-2:]))

    CALLS["get_activities"].clear()
    scripted = use_script([{"text": "", "tools": [("get_activities", {"limit": 3})]},
                           {"text": "", "tools": [("get_activities", {"limit": 3})]}, {"text": "ok"}])
    client.post("/app/api/chat/send", headers=bart, json={"text": "Twice"})
    check("the same call twice in one message runs once", CALLS["get_activities"].count({"limit": 3}) == 1
          and scripted.results[1][0][2] is True, str(scripted.results))

    use_script([{"text": "Your week was ", "raise": True}])
    r = client.post("/app/api/chat/send", headers=bart, json={"text": "Tell me"})
    got = events(r.text)
    check("a stream that dies ends with a short error, not a hang",
          got[-1][0] == "error" and got[-1][1]["code"] == "provider" and "Try again" in got[-1][1]["message"], str(got[-2:]))
    last = client.get("/app/api/chat", headers=bart).json()["messages"][-1]
    check("and what was said so far is saved as partial", last["status"] == "partial" and last["text"] == "Your week was ",
          str(last))

    os.environ["CHAT_DAILY_MESSAGE_CAP"] = str(store.chat_sent_since(store.email_fingerprint("bart@example.com"), 0))
    use_script([{"text": "never"}])
    r = client.post("/app/api/chat/send", headers=bart, json={"text": "One more"})
    check("past the daily cap: 429 with a clear message", r.status_code == 429 and r.json()["code"] == "cap"
          and "today's limit" in r.json()["error"], r.text)
    os.environ.pop("CHAT_DAILY_MESSAGE_CAP")
    check("the cap defaults to 500", chat_pilot.env()["daily_cap"] == 500)

    # -- feedback, a new thread, privacy ---------------------------------------------------
    answer = next(m for m in client.get("/app/api/chat", headers=bart).json()["messages"] if m["role"] == "assistant"
                  and m["text"])
    r = client.post("/app/api/chat/feedback", headers=bart, json={"id": answer["id"], "value": "down"})
    check("thumbs down is kept", r.status_code == 200 and any(
        m.get("feedback") == "down" for m in client.get("/app/api/chat", headers=bart).json()["messages"]))
    check("chat_feedback is counted", posted("chat_feedback")[-1]["properties"].get("value") == "down")
    check("nothing personal goes to PostHog: no message text on any chat event",
          not any("Four runs" in json.dumps(e) or "Delete old tempo" in json.dumps(e) for e in SENT))
    with store._connect() as conn:
        raw = conn.execute("SELECT content_enc FROM chat_messages WHERE content_enc IS NOT NULL LIMIT 1").fetchone()[0]
    check("messages are encrypted at rest", b"Four runs" not in raw and b"How did" not in raw)
    client.post("/app/api/chat/new", headers=bart, json={})
    check("New chat starts an empty thread", client.get("/app/api/chat", headers=bart).json()["messages"] == [])

    import anyio

    anyio.run(hosted.forget_person, bart_token)
    with store._connect() as conn:
        left = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE person = ?",
                            (store.email_fingerprint("bart@example.com"),)).fetchone()[0]
    check("deleting the account deletes the chat", left == 0, str(left))

    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


def SENT_KINDS(event: str) -> list[dict]:  # noqa: N802 - read as a constant-ish helper in checks
    return [e for e in SENT if e["event"] == event]


if __name__ == "__main__":
    raise SystemExit(main())
