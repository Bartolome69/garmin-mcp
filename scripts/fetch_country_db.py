"""Fetch DB-IP's free IP to Country Lite table for the server image (garmin_mcp/geo.py).

CC BY 4.0, https://db-ip.com. Published monthly, so this month's is tried,
then last month's. Never fails the build: without a table the server records
sign-ups without a country.
"""

import datetime
import gzip
import sys
import urllib.request

dest = sys.argv[1] if len(sys.argv) > 1 else "/app/geo/dbip-country-lite.mmdb"
today = datetime.date.today()
last = today.replace(day=1) - datetime.timedelta(days=1)
for month in (today.strftime("%Y-%m"), last.strftime("%Y-%m")):
    url = f"https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
    try:
        with urllib.request.urlopen(url, timeout=60) as answer:
            data = gzip.decompress(answer.read())
        with open(dest, "wb") as out:
            out.write(data)
        print(f"country table {month}: {len(data) // 1024} KB")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"no country table for {month} ({type(exc).__name__})")
