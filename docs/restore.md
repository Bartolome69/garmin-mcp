# Restoring the database

The exact commands. The background (what's backed up, what each kind of
failure needs, what never to do) is in [RECOVERY.md](RECOVERY.md).

App: `garmin-mcp-1b4387`. Database: `/data/garmin-mcp.sqlite3` on the
`garmin_data` volume. No secret values belong in this file: the repository is
public.

## 0. Switch continuous backup on (once)

Litestream is in the image and copies every change to object storage within
seconds, once it has a bucket. The simplest bucket is Tigris, on Fly itself:

```sh
fly storage create -a garmin-mcp-1b4387 -n daash-backup
```

That makes a private bucket and sets four secrets on the app (`BUCKET_NAME`,
`AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`), which
restarts the server. `scripts/start.sh` picks them up and starts the server
under `litestream replicate`. Copy the four values into the password manager
(`fly ssh console -a garmin-mcp-1b4387 -C 'printenv BUCKET_NAME AWS_ENDPOINT_URL_S3 AWS_ACCESS_KEY_ID'`).

A bucket elsewhere (Cloudflare R2, Backblaze B2, AWS S3) works too: set
`LITESTREAM_BUCKET`, `LITESTREAM_ENDPOINT`, `LITESTREAM_ACCESS_KEY_ID` and
`LITESTREAM_SECRET_ACCESS_KEY` instead (RECOVERY.md).

Then run the **Restore drill** workflow (step 1 below does the same by hand).

## 1. Prove a restore works (read-only, any time)

The **Restore drill** workflow (Actions → Restore drill → Run workflow) does
this and prints the size and how long each restore took. By hand:

```sh
fly ssh console -a garmin-mcp-1b4387
# on the machine:
litestream restore -config /app/litestream.yml -o /tmp/restore-test.db /data/garmin-mcp.sqlite3
sqlite3 /tmp/restore-test.db 'PRAGMA integrity_check; SELECT COUNT(*) FROM users;'
rm -f /tmp/restore-test.db*
```

If `litestream` says the config has no bucket, run it with the Tigris names
mapped first: `export LITESTREAM_BUCKET=$BUCKET_NAME LITESTREAM_ENDPOINT=$AWS_ENDPOINT_URL_S3 LITESTREAM_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID LITESTREAM_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY LITESTREAM_REGION=auto`.

Never point a restored copy at Garmin while the live server runs: refreshing a
session in the copy retires the live one's token.

## 2. The volume is lost or the database is damaged

The server restores itself on boot: `scripts/start.sh` runs
`litestream restore -if-db-not-exists -if-replica-exists` before starting, so
an empty volume is filled from the bucket and a volume that has a database is
never touched.

```sh
APP=garmin-mcp-1b4387
fly machine list -a $APP                                  # note the machine id
fly machine stop <machine-id> -a $APP                     # nothing takes sign-ins during the swap
fly volumes create garmin_data -a $APP --region lhr --size 1   # empty
fly machine clone <machine-id> -a $APP --attach-volume <new-volume-id>:/data
fly logs -a $APP                                          # "restoring snapshot", then the server starting
```

Check `https://garmin-mcp-1b4387.fly.dev/` answers and a question in Claude
works, then `fly machine destroy <old-machine-id> -a $APP`. Keep the old
volume a week before `fly volumes destroy`.

## 3. Go back to a moment in time (bad data written)

Every change is kept 30 days (`retention: 720h` in `litestream.yml`).

```sh
fly ssh console -a garmin-mcp-1b4387
litestream snapshots -config /app/litestream.yml /data/garmin-mcp.sqlite3   # what's there
litestream restore -config /app/litestream.yml -timestamp 2026-11-25T07:00:00Z \
  -o /data/restored.sqlite3 /data/garmin-mcp.sqlite3
```

Then swap it in with the server stopped: `fly machine stop`, start a one-off
console on the volume, move `garmin-mcp.sqlite3` aside (with its `-wal` and
`-shm`), rename `restored.sqlite3` to it, and start the machine. Take a volume
snapshot first (`fly volumes snapshots create <volume-id>`).

## 4. Without Litestream

Use the newest Fly snapshot (daily, kept 30 days, plus one before every
deploy): `fly volumes snapshots list <volume-id>`, then step 2 with
`--snapshot-id <id>` on `fly volumes create`.

## The key

Every stored session and token is encrypted with `GARMIN_MCP_SECRET`. A backup
is useless without the exact key it was written with, so keep a copy in the
password manager (RECOVERY.md says how). Never set a new one.
