"""Fetch DB-IP's free IP to Country Lite table for the server image (garmin_mcp/geo.py).

CC BY 4.0, https://db-ip.com. Published monthly, so this month's is tried,
then last month's. db-ip.com turned Fly's builder away on Oct 26 (an HTTP
error for both months), so the same table is then taken from the npm
registry, where the ip-location-db project republishes it. Never fails the
build: without a table the server records sign-ups without a country.
"""

import datetime
import gzip
import io
import json
import sys
import tarfile
import urllib.request

AGENT = {"User-Agent": "Mozilla/5.0 (compatible; Daash build; +https://daash.run)"}
NPM = "https://registry.npmjs.org/@ip-location-db/dbip-country-mmdb/latest"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=60) as answer:
        return answer.read()


def from_db_ip() -> bytes | None:
    today = datetime.date.today()
    last = today.replace(day=1) - datetime.timedelta(days=1)
    for month in (today.strftime("%Y-%m"), last.strftime("%Y-%m")):
        try:
            data = gzip.decompress(fetch(f"https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"))
            print(f"country table {month} from db-ip.com: {len(data) // 1024} KB")
            return data
        except Exception as exc:  # noqa: BLE001
            print(f"no country table for {month} from db-ip.com ({type(exc).__name__}: {getattr(exc, 'code', exc)})")
    return None


def from_npm() -> bytes | None:
    try:
        release = json.loads(fetch(NPM))
        with tarfile.open(fileobj=io.BytesIO(fetch(release["dist"]["tarball"])), mode="r:gz") as pack:
            data = pack.extractfile("package/dbip-country.mmdb").read()
        print(f"country table {release['version']} from npm: {len(data) // 1024} KB")
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"no country table from npm ({type(exc).__name__}: {exc})")
        return None


dest = sys.argv[1] if len(sys.argv) > 1 else "/app/geo/dbip-country-lite.mmdb"
data = from_db_ip() or from_npm()
if data:
    with open(dest, "wb") as out:
        out.write(data)
