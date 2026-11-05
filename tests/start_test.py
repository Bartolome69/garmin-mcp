"""How the hosted server starts: under Litestream only when backup is set up.

Runs scripts/start.sh with stand-in `litestream` and `python` commands that
record how they were called, so nothing real starts.

    python tests/start_test.py
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "scripts" / "start.sh"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def run(env_extra: dict[str, str]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        log = Path(tmp) / "calls"
        for name in ("litestream", "python"):
            stub = bin_dir / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
            stub.chmod(0o755)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LITESTREAM_CONFIG": "/cfg.yml", **env_extra}
        subprocess.run(["sh", str(START)], env=env, check=True, timeout=20)
        return log.read_text().splitlines() if log.exists() else []


print("without backup set up")
calls = run({})
check("the server starts as it always did, and Litestream is never called",
      calls == ["python -m garmin_mcp.hosted"], str(calls))

print("\nwith a bucket set")
calls = run({"LITESTREAM_BUCKET": "daash-backup", "GARMIN_MCP_DB": "/data/db.sqlite3"})
check("first it restores, but only onto an empty volume and only from a replica that exists",
      len(calls) == 2 and calls[0] == "litestream restore -config /cfg.yml -if-db-not-exists -if-replica-exists /data/db.sqlite3",
      str(calls))
check("then it replicates, with the server as its child",
      len(calls) == 2 and calls[1] == "litestream replicate -config /cfg.yml -exec python -m garmin_mcp.hosted", str(calls))

print("\nthe database path")
calls = run({"LITESTREAM_BUCKET": "daash-backup"})
check("defaults to the volume's database when none is set",
      calls[:1] == ["litestream restore -config /cfg.yml -if-db-not-exists -if-replica-exists /data/garmin-mcp.sqlite3"],
      str(calls))

print("\nthe config")
text = (ROOT / "litestream.yml").read_text()
check("names no bucket, endpoint or key itself: they come from Fly secrets",
      all(f"${{{v}}}" in text for v in ("LITESTREAM_BUCKET", "LITESTREAM_ENDPOINT", "LITESTREAM_ACCESS_KEY_ID",
                                        "LITESTREAM_SECRET_ACCESS_KEY", "GARMIN_MCP_DB")))
docker = (ROOT / "Dockerfile").read_text()
check("the image starts through this script", 'CMD ["sh", "/app/scripts/start.sh"]' in docker)
check("and pins Litestream to a checksum", "LITESTREAM_SHA256=" in docker and "sha256sum -c" in docker)

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")
