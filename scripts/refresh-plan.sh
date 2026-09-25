#!/bin/bash
# Regenerate the plan page and publish it, if anything changed.
#
# Run by launchd each morning. Deliberately narrow: it only ever commits the
# generated page, so nothing else in the working tree can be swept up by a
# scheduled job running unattended.

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

"$PROJECT/.venv/bin/python" -m garmin_mcp.plan

# Stage first, then compare against the index. `git diff` alone ignores
# untracked files, so before the page was ever committed this always reported
# "unchanged" and published nothing.
git add -- docs/p

if git diff --cached --quiet -- docs/p; then
    echo "plan unchanged; nothing to publish"
    exit 0
fi

git commit -q -m "Refresh plan view ($(date +%Y-%m-%d))"
git push -q origin main
echo "published"
