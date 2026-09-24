# Hosted, multi-user server. The local install needs none of this.
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY requirements.txt constraints-legacy.txt ./
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt \
    && pip install --no-cache-dir uvicorn

COPY garmin_mcp/ ./garmin_mcp/

# The token database lives on a mounted volume so sign-ins survive a redeploy.
ENV GARMIN_MCP_DB=/data/garmin-mcp.sqlite3 \
    PORT=8000 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
USER app
EXPOSE 8000

CMD ["python", "-m", "garmin_mcp.hosted"]
