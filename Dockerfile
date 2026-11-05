# Hosted, multi-user server. The local install needs none of this.
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY requirements-hosted.txt ./
RUN pip install --no-cache-dir --only-binary :all: -r requirements-hosted.txt

# Country lookups for sign-ups (garmin_mcp/geo.py): DB-IP's free IP to Country
# Lite table, CC BY 4.0, https://db-ip.com. It's published monthly, so this
# month's is tried, then last month's; without either the server still runs
# and records sign-ups without a country.
COPY scripts/fetch_country_db.py ./scripts/
RUN mkdir -p /app/geo && python scripts/fetch_country_db.py /app/geo/dbip-country-lite.mmdb

# Litestream copies every change to the database, within seconds, to storage
# off Fly (docs/RECOVERY.md). It runs only when LITESTREAM_BUCKET is set; the
# archive is pinned to the checksum of v0.3.13 as GitHub serves it.
ARG LITESTREAM_VERSION=v0.3.13
ARG LITESTREAM_SHA256=eb75a3de5cab03875cdae9f5f539e6aedadd66607003d9b1e7a9077948818ba0
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/benbjohnson/litestream/releases/download/${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-amd64.tar.gz', '/tmp/litestream.tar.gz')" \
    && echo "${LITESTREAM_SHA256}  /tmp/litestream.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream \
    && rm /tmp/litestream.tar.gz
COPY litestream.yml ./
COPY scripts/start.sh ./scripts/

COPY garmin_mcp/ ./garmin_mcp/

# The token database lives on a mounted volume so sign-ins survive a redeploy.
ENV GARMIN_MCP_DB=/data/garmin-mcp.sqlite3 \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
USER app
EXPOSE 8000

CMD ["sh", "/app/scripts/start.sh"]
