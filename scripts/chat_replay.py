"""Replay the top real request types through the chat pilot, and log what each cost.

One realistic prompt for each of the most-used MCP tools in PostHog (get_activities,
get_plan, create_workout, create_plan, find_exercises, get_readiness), sent through
the real chat endpoint for an allowlisted account (Bart's). For each: which tools
the model called, whether the first one matches the tool the prompt stands for,
tokens, latency and estimated cost. Real model calls, so it costs a few cents.

Writes go through the confirm flow and are DECLINED by default: nothing reaches
Garmin. The write prompts name a date about ten weeks out, so even with --confirm
they land somewhere visible and easy to remove, not on this week.

    # Against the live server, as Bart (the token is the app's: localStorage "daash.access"
    # in the browser's dev tools, on the Daash app, signed in):
    DAASH_TOKEN=... python scripts/chat_replay.py
    DAASH_TOKEN=... python scripts/chat_replay.py --out replay.jsonl --keep-thread

    # Locally, against the demo runner (scripts/app_dev.py with DEMO_CHAT=1 and a key):
    DAASH_TOKEN=<the dev-token it prints> python scripts/chat_replay.py --base http://localhost:8787

Each prompt starts a fresh chat thread (POST /app/api/chat/new), so one answer doesn't
lean on another; the thread before it is kept, not deleted. --keep-thread sends them all
into the current thread instead. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any


def test_dates() -> tuple[str, str]:
    """A Thursday and a Monday about ten weeks out: clear of the plan running now."""
    base = date.today() + timedelta(weeks=10)
    monday = base - timedelta(days=base.weekday())
    return (monday + timedelta(days=3)).isoformat(), monday.isoformat()


def prompts() -> list[dict[str, str]]:
    thursday, monday = test_dates()
    return [
        {"tool": "get_activities", "text": "How did my runs go this week?"},
        {"tool": "get_plan", "text": "What's on my plan tomorrow?"},
        {"tool": "create_workout", "text": f"Make me a 45 min tempo for Thursday {thursday} and send it to my watch"},
        {"tool": "create_plan", "text": f"Build a 12-week half plan, 4 days a week, starting Monday {monday}"},
        {"tool": "find_exercises", "text": "Give me a 30 min strength session"},
        {"tool": "get_readiness", "text": "Am I recovered enough for intervals today?"},
    ]


def call(base: str, token: str, path: str, body: dict[str, Any] | None = None, stream: bool = False):
    request = urllib.request.Request(
        f"{base}/app/api/{path}", data=json.dumps(body or {}).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "text/event-stream" if stream else "application/json"})
    return urllib.request.urlopen(request, timeout=180)


def events(response) -> Any:
    """(event, data) pairs from the chat's server-sent events, as they arrive."""
    name, data = "", []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\n")
        if not line:
            if data:
                yield name, json.loads("\n".join(data))
            name, data = "", []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


def run_one(base: str, token: str, prompt: dict[str, str], confirm: bool, fresh: bool) -> dict[str, Any]:
    if fresh:
        call(base, token, "chat/new", {}).read()
    started = time.monotonic()
    row: dict[str, Any] = {"expected_tool": prompt["tool"], "prompt": prompt["text"], "tools": [], "proposed": [],
                           "answer": "", "status": "?", "first_token_ms": None}
    try:
        with call(base, token, "chat/send", {"text": prompt["text"]}, stream=True) as response:
            for name, data in events(response):
                if name == "delta" and row["first_token_ms"] is None:
                    row["first_token_ms"] = int((time.monotonic() - started) * 1000)
                if name == "delta":
                    row["answer"] += data.get("text", "")
                elif name == "pending":
                    row["proposed"].append({"id": data["id"], "tool": data["tool"], "summary": data["text"]})
                elif name in ("done", "error"):
                    row["status"] = data.get("status") or f"error:{data.get('code')}"
                    row.update({k: v for k, v in (data.get("usage") or {}).items()})
    except urllib.error.HTTPError as exc:
        row["status"] = f"http {exc.code}: {exc.read().decode()[:200]}"
        return row
    row["matched_tool"] = (row.get("tools") or ["none"])[0]
    row["match"] = row["matched_tool"] == prompt["tool"] or (
        # A plan or a workout is made with a write: proposing it is the match.
        any(p["tool"] == prompt["tool"] for p in row["proposed"]))
    for p in row["proposed"]:
        decision = "confirm" if confirm else "decline"
        try:
            answer = json.loads(call(base, token, "chat/action", {"id": p["id"], "decision": decision}).read())
            p["outcome"] = answer.get("status")
        except urllib.error.HTTPError as exc:
            p["outcome"] = f"http {exc.code}: {exc.read().decode()[:120]}"
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=os.environ.get("DAASH_BASE", "https://mcp.garmin.daash.run"))
    parser.add_argument("--token", default=os.environ.get("DAASH_TOKEN", ""))
    parser.add_argument("--confirm", action="store_true",
                        help="confirm proposed changes (they reach Garmin, on the test dates) instead of declining them")
    parser.add_argument("--keep-thread", action="store_true", help="send every prompt into the current thread")
    parser.add_argument("--out", help="also write one JSON line per request here")
    args = parser.parse_args()
    if not args.token:
        print("Set DAASH_TOKEN (or --token) to the app's access token for the allowlisted account.", file=sys.stderr)
        return 2
    base = args.base.rstrip("/")
    try:
        if not json.loads(call(base, args.token, "features").read()).get("chat"):
            print("Chat isn't on for this account (CHAT_PILOT_USER_IDS).", file=sys.stderr)
            return 2
    except urllib.error.HTTPError as exc:
        print(f"Couldn't reach {base}: {exc.code}", file=sys.stderr)
        return 2
    if args.confirm:
        print("--confirm: proposed changes WILL be made on Garmin, on the test dates.\n")
    rows = []
    out = open(args.out, "a") if args.out else None
    try:
        for prompt in prompts():
            row = run_one(base, args.token, prompt, args.confirm, fresh=not args.keep_thread)
            rows.append(row)
            if out:
                out.write(json.dumps({"at": int(time.time()), **row}) + "\n")
            cost = row.get("est_cost_usd")
            print(f"{prompt['tool']:<15} {'match' if row.get('match') else 'MISS ':<5} "
                  f"tools={','.join(row.get('tools') or []) or '-':<40} "
                  f"in={row.get('input_tokens', '?'):>6} out={row.get('output_tokens', '?'):>5} "
                  f"{row.get('latency_ms', '?'):>6}ms ${cost if cost is not None else '?'}  {row['status']}")
            for p in row["proposed"]:
                print(f"{'':<22}proposed {p['tool']}: {p['summary'][:110]} -> {p.get('outcome')}")
    finally:
        if out:
            out.close()
    costs = [r["est_cost_usd"] for r in rows if r.get("est_cost_usd") is not None]
    tokens_in = sum(r.get("input_tokens") or 0 for r in rows)
    tokens_out = sum(r.get("output_tokens") or 0 for r in rows)
    latencies = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    print(f"\n{sum(1 for r in rows if r.get('match'))}/{len(rows)} matched the expected tool; "
          f"{tokens_in} input and {tokens_out} output tokens; est. ${sum(costs):.4f} in all"
          + (f"; median latency {latencies[len(latencies) // 2]} ms" if latencies else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
