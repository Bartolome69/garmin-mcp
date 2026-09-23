#!/usr/bin/env python3
"""Wrap the artifact page source into a standalone document for GitHub Pages.

The artifact host supplies a document skeleton; a self-hosted copy needs its
own. Generating rather than hand-editing keeps the two versions identical.

    python3 scripts/build-page.py SOURCE.html docs/index.html
"""

import sys
from pathlib import Path

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Connect Claude to your Garmin Connect account: read your runs, splits and heart rate, and write structured workouts onto your watch.">
<meta property="og:title" content="Garmin for Claude">
<meta property="og:description" content="Let Claude read your running data and build workouts onto your watch.">
<meta property="og:type" content="website">
<style>
  html { color-scheme: light; }
  body { margin: 0; }
  img { max-width: 100%; }
  [hidden] { display: none !important; }
</style>
"""


def main() -> int:
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    body = source.read_text()

    marker = "</style>\n"
    if marker not in body:
        print("No </style> found; is this the artifact source?", file=sys.stderr)
        return 1
    split = body.index(marker) + len(marker)
    head, page = body[:split], body[split:]

    dest.write_text(HEAD + head + "</head>\n<body>\n" + page + "\n</body>\n</html>\n")
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
