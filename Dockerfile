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

COPY garmin_mcp/ ./garmin_mcp/

# The token database lives on a mounted volume so sign-ins survive a redeploy.
ENV GARMIN_MCP_DB=/data/garmin-mcp.sqlite3 \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
USER app
EXPOSE 8000

CMD ["python", "-m", "garmin_mcp.hosted"]
