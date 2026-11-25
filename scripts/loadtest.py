#!/usr/bin/env python3
"""Replay a busy hour against Daash with a stand-in Garmin (Nov 25). Never production.

The peak hour now is about 460 tool calls (16:00 and 19:00 London), in bursts.
This sends that hour's mix of tools from many people at the same rate, with
bursts, to a server whose Garmin is the stand-in in garmin_mcp/loadfake.py
(each read 1-30 s, 2% rate-limited), and reports p95 latency, errors by
kind, the server's memory and the Garmin pool's queue depth.

Two ways to run it:

  Local (starts the real server, web and worker, on a scratch database):
      python scripts/loadtest.py --local --minutes 10

  Against a staging app running with GARMIN_MCP_FAKE_GARMIN=load:
      python scripts/loadtest.py --url https://<staging>.fly.dev --tokens tokens.txt
  tokens.txt holds one /u/<token>/mcp token per line, for accounts made on
  staging. Memory and queue depth then come from staging's server_heartbeat
  in PostHog; the script reports latency and errors.

It refuses daash.run, mcp.garmin.daash.run and the production app's address.

Options: --calls-per-hour (460), --minutes (60), --users (40, local),
--latency "1,30", --share-429 0.02, --bursts (on).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION = ("daash.run", "garmin-mcp-1b4387")

# Last 7 days' calls by tool (tool_timing, Nov 25), reads people make in chat.
MIX = [
    ("get_activities", {}, 6272),
    ("get_daily_summary", {"date": "today"}, 3321),
    ("get_activity_details", {"activity_id": 1111}, 3312),
    ("get_sleep_data", {"date": "today"}, 2915),
    ("get_readiness", {}, 2703),
    ("get_plan", {}, 1986),
    ("get_recovery_trends", {}, 863),
    ("get_workout", {"workout_id": 555001}, 807),
    ("get_fitness", {}, 564),
    ("get_fitness_trends", {"months": 6}, 456),
]


def pick() -> tuple[str, dict]:
    total = sum(w for _, _, w in MIX)
    n = random.uniform(0, total)
    for name, args, weight in MIX:
        n -= weight
        if n <= 0:
            return name, args
    return MIX[0][0], MIX[0][1]


def classify(status: int, body: str) -> str | None:
    """None for success, else a short kind."""
    if status != 200:
        return f"http_{status}"
    text = body
    if "Garmin is slow right now" in text:
        return "garmin_slow"
    if "very busy right now" in text or "still waiting on Garmin" in text:
        return "pool_busy"
    if "rate limit" in text.lower() or "limiting how often" in text:
        return "rate_limited"
    if '"isError":true' in text.replace(" ", "") or '\\"error\\"' in text or '"error"' in text:
        return "tool_error"
    return None


class Load:
    def __init__(self, base: str, tokens: list[str], rate_per_hour: float, minutes: float, bursts: bool) -> None:
        self.base, self.tokens = base.rstrip("/"), tokens
        self.rate, self.minutes, self.bursts = rate_per_hour, minutes, bursts
        self.results: list[tuple[float, str, float, str | None]] = []  # (t, tool, seconds, error kind)
        self.inflight = 0
        self.peak_inflight = 0
        self.examples: dict[str, str] = {}  # one answer per error kind, to read

    @staticmethod
    def _post(url: str, body: dict) -> tuple[int, str]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode(errors="replace")

    async def call(self, token: str) -> None:
        tool, args = pick()
        url = f"{self.base}/u/{token}/mcp"
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        started = time.monotonic()
        self.inflight += 1
        self.peak_inflight = max(self.peak_inflight, self.inflight)
        try:
            status, text = await asyncio.to_thread(self._post, url, body)
            kind = classify(status, text)
            if kind and kind not in self.examples:
                self.examples[kind] = f"{tool}: {text[-220:]}"
        except Exception as exc:  # noqa: BLE001
            kind = f"client_{type(exc).__name__}"
        finally:
            self.inflight -= 1
        self.results.append((time.time(), tool, time.monotonic() - started, kind))

    async def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=300))
        end = time.monotonic() + self.minutes * 60
        per_s = self.rate / 3600
        first = min(120.0, self.minutes * 30)  # a burst within the first half of a short run too
        next_burst = time.monotonic() + random.uniform(first, max(first, min(480.0, self.minutes * 45))) \
            if self.bursts else float("inf")
        tasks = []
        weights = [1 / (i + 1) for i in range(len(self.tokens))]  # a few heavy users, many light
        while time.monotonic() < end:
            await asyncio.sleep(random.expovariate(per_s))
            n = 1
            if time.monotonic() >= next_burst:
                n = random.randint(8, 20)  # several people at once
                next_burst = time.monotonic() + random.uniform(300, 600)
            for _ in range(n):
                token = random.choices(self.tokens, weights)[0]
                tasks.append(asyncio.create_task(self.call(token)))
        await asyncio.gather(*tasks)


def _rss_mb(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024)
    except OSError:
        return None
    return None


def _children(pid: int) -> dict[str, int]:
    found = {}
    for p in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = Path(f"/proc/{p}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if "garmin_mcp.hosted" in cmd:
            found["web"] = int(p)
        elif "garmin_mcp.worker" in cmd:
            found["worker"] = int(p)
    return found


async def sample(stop: asyncio.Event, note: Path | None, out: list[dict]) -> None:
    while not stop.is_set():
        row = {"t": time.time()}
        if note is not None:
            procs = _children(os.getpid())
            row.update({f"{k}_rss_mb": _rss_mb(v) for k, v in procs.items()})
            try:
                load = json.loads(note.read_text())
                row.update(queue=load.get("queue"), busy=load.get("busy"))
            except (OSError, ValueError):
                pass
        out.append(row)
        try:
            await asyncio.wait_for(stop.wait(), 5)
        except asyncio.TimeoutError:
            pass


def start_local(users: int, port: int, latency: str, share_429: float) -> tuple[subprocess.Popen, list[str], Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="daash-load-"))
    from cryptography.fernet import Fernet

    env = {**os.environ, "GARMIN_MCP_DB": str(tmp / "load.sqlite3"),
           "GARMIN_MCP_SECRET": Fernet.generate_key().decode(), "PORT": str(port),
           "GARMIN_MCP_FAKE_GARMIN": "load", "GARMIN_FAKE_LATENCY": latency, "GARMIN_FAKE_429": str(share_429),
           "DAASH_LOAD_NOTE": str(tmp / "load.json"), "PYTHONPATH": str(ROOT),
           "GARMIN_MCP_OAUTH": "", "GARMIN_MCP_POSTHOG_KEY": "", "FLY_APP_NAME": "daash-load-test"}
    seed = subprocess.run(
        [sys.executable, "-c",
         "import sys; from garmin_mcp import store; "
         f"print('\\n'.join(store.save_user('l***@example.com', 'fake', email_hash=f'load-{{i}}') for i in range({users})))"],
        env=env, capture_output=True, text=True, check=True, cwd=ROOT)
    tokens = seed.stdout.split()
    proc = subprocess.Popen([sys.executable, "-m", "garmin_mcp.supervise"], env=env, cwd=ROOT,
                            stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT)
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return proc, tokens, tmp / "load.json", tmp


def report(load: Load, samples: list[dict], minutes: float) -> None:
    rows = load.results
    took = sorted(r[2] for r in rows)
    kinds = Counter(r[3] for r in rows if r[3])

    def q(p: float) -> float:
        return took[min(len(took) - 1, int(p * len(took)))] if took else 0.0

    print(f"\n=== {len(rows)} tool calls in {minutes:g} min ({len(rows) * 60 / minutes:.0f}/hour) ===")
    print(f"latency: p50 {q(.5):.1f} s, p95 {q(.95):.1f} s, max {took[-1] if took else 0:.1f} s")
    ok = len(rows) - sum(kinds.values())
    print(f"ok: {ok} ({100 * ok / max(1, len(rows)):.1f}%)")
    for kind, n in kinds.most_common():
        print(f"  {kind}: {n}   e.g. {load.examples.get(kind, '')[:200]!r}")
    print(f"most calls in flight at once: {load.peak_inflight}")
    web = [s.get("web_rss_mb") for s in samples if s.get("web_rss_mb")]
    wrk = [s.get("worker_rss_mb") for s in samples if s.get("worker_rss_mb")]
    queue = [s.get("queue") for s in samples if s.get("queue") is not None]
    busy = [s.get("busy") for s in samples if s.get("busy") is not None]
    if web:
        print(f"web memory: start {web[0]} MB, peak {max(web)} MB, end {web[-1]} MB")
    if wrk:
        print(f"worker memory: peak {max(wrk)} MB")
    if queue:
        print(f"Garmin pool: queue depth peak {max(queue)}, busy workers peak {max(busy) if busy else '?'}")
    if not web:
        print("memory and queue depth: see server_heartbeat from the staging app in PostHog")
    print("\nby 5 minutes:")
    if rows:
        t0 = rows[0][0]
        buckets: dict[int, list] = {}
        for r in rows:
            buckets.setdefault(int((r[0] - t0) // 300), []).append(r)
        mem: dict[int, list[dict]] = {}
        for smp in samples:
            mem.setdefault(int((smp["t"] - t0) // 300), []).append(smp)
        for b, rs in sorted(buckets.items()):
            ts = sorted(x[2] for x in rs)
            errs = sum(1 for x in rs if x[3])
            web_b = [m.get("web_rss_mb") for m in mem.get(b, []) if m.get("web_rss_mb")]
            wrk_b = [m.get("worker_rss_mb") for m in mem.get(b, []) if m.get("worker_rss_mb")]
            memory = (f", web {max(web_b)} MB, worker {max(wrk_b) if wrk_b else '?'} MB" if web_b else "")
            print(f"  {b * 5:>3}-{b * 5 + 5:<3} min: {len(rs):>4} calls, p95 {ts[min(len(ts) - 1, int(.95 * len(ts)))]:.1f} s, "
                  f"{errs} errors{memory}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--tokens")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--calls-per-hour", type=float, default=460)
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--latency", default="1,30")
    ap.add_argument("--share-429", type=float, default=0.02)
    ap.add_argument("--no-bursts", action="store_true")
    args = ap.parse_args()

    proc = None
    note = None
    if args.local:
        proc, tokens, note, tmp = start_local(args.users, args.port, args.latency, args.share_429)
        base = f"http://127.0.0.1:{args.port}"
        print(f"local server on {base}, {len(tokens)} people, Garmin stand-in {args.latency} s, "
              f"{args.share_429:.0%} 429s; log {tmp / 'server.log'}")
    else:
        if not args.url or not args.tokens:
            ap.error("give --local, or --url and --tokens")
        host = urlparse(args.url).hostname or ""
        if any(p in host for p in PRODUCTION):
            print(f"Refusing: {host} is production. Run this against a staging app only.")
            return 2
        base = args.url
        tokens = [t.strip() for t in Path(args.tokens).read_text().split() if t.strip()]

    load = Load(base, tokens, args.calls_per_hour, args.minutes, not args.no_bursts)
    samples: list[dict] = []
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample(stop, note, samples))
    try:
        await load.run()
    finally:
        stop.set()
        await sampler
        if proc is not None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    report(load, samples, args.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
