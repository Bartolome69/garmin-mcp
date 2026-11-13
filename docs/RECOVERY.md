# Recovering the hosted server

Everything people have connected lives in one SQLite database on one Fly
volume (`garmin_data`, mounted at `/data`, app `garmin-mcp-1b4387`): every
account, every Claude and ChatGPT token, every Garmin session, and every link
to Garmin's official API. Losing it signs everyone out. Losing an official
link is worse: while Garmin's limit on new connections is full, that person
can't link again until Garmin raises it.

No secret values belong in this file. The repository is public.

## What protects it

| Protection | What it covers | How much can be lost |
|---|---|---|
| Fly's daily volume snapshots, kept 30 days | Volume lost or damaged; a bad day | Up to a day |
| A snapshot taken by every deploy, just before it | A deploy that damages data | Minutes |
| Litestream, copying every change off Fly (once set up, below) | Volume, machine or Fly account lost | Seconds |
| The server refuses to start with the wrong key | A mistyped or replaced `GARMIN_MCP_SECRET` | Nothing: it stops instead |
| Ending an official link takes two calls | An accidental `garmin_official` disconnect | Nothing without the second call |

A day-old copy is weaker than it looks: Garmin replaces sign-in tokens as they
refresh, so some of a day's old tokens no longer work and those people have to
sign in again. That's why Litestream matters.

## The key: keep a copy off Fly

`GARMIN_MCP_SECRET` encrypts every stored session, token and email. Without
the exact key the database was written with, every backup is unreadable. It
exists only in Fly's secrets, so keep a copy in a password manager:

```sh
fly ssh console -a garmin-mcp-1b4387 -C 'printenv GARMIN_MCP_SECRET'
```

Run that on your own computer and paste the value into the password manager.
Never set a new value: the server refuses to start with a key that can't open
what's stored, and a new key would sign everyone out.

The other settings can be got back from elsewhere: the official API's client
ID and secret from Garmin's developer portal and GitHub's repository secrets
(which the deploy stages onto Fly each time), the sign-in relay from IPRoyal,
the analytics key from PostHog, mail keys from Resend. A copy of the relay
address (`GARMIN_MCP_LOGIN_PROXY`) in the password manager saves a hunt.

## Setting up off-site backup (Litestream)

Litestream is in the image and switched off until it has somewhere to write.
To switch it on, once:

1. Create a private bucket on Cloudflare R2 (or Backblaze B2, or AWS S3), for
   example `daash-backup`.
2. Create an access key that can read and write that bucket only.
3. Set them on Fly from your own computer (this restarts the server, which
   then starts copying):

   ```sh
   fly secrets set -a garmin-mcp-1b4387 \
     LITESTREAM_BUCKET=daash-backup \
     LITESTREAM_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com \
     LITESTREAM_ACCESS_KEY_ID=<key id> \
     LITESTREAM_SECRET_ACCESS_KEY=<secret>
   ```

   For AWS S3, leave out the endpoint and set `LITESTREAM_REGION` instead.
4. Put the four values in the password manager too.
5. Run the **Restore drill** workflow. It should report the off-site copy as
   0 to 2 accounts behind live.

`scripts/start.sh` starts the server under Litestream whenever
`LITESTREAM_BUCKET` is set. On an empty volume it restores from the bucket
first; on a volume that has a database it never touches it.

## When something goes wrong

**The server crashed or restarted.** Nothing to recover: the database is on
the volume. Fly restarts it. Look at the **Server error lines** workflow for
why.

**It won't start, and the log says `GARMIN_MCP_SECRET can't open any stored
session`.** The key changed. Set it back to the copy in the password manager:
`fly secrets set -a garmin-mcp-1b4387 GARMIN_MCP_SECRET=<the stored value>`.
Nothing was lost; it stopped before touching anyone.

**A deploy broke Daash, but the data is fine.** Deploy the commit before it:
[ROLLBACK.md](ROLLBACK.md).

**A deploy damaged the data.** Restore the snapshot that deploy took (it's
the newest one from just before the deploy's time) with the volume swap
below.

**The volume is lost or the database is damaged.** With Litestream set up:
make an empty volume and swap it in (below); the server restores itself from
the bucket as it starts. Without it: swap in a volume made from the newest
snapshot.

**The Fly account or app is lost.** Make a new app with a `garmin_data`
volume, set the secrets from the password manager and GitHub, set the
Litestream secrets, and deploy: it restores from the bucket. Claude and
ChatGPT connect by the address `mcp.garmin.daash.run`, so point that domain
at the new app.

**Someone's official link was ended by mistake.** It can't be restored from a
backup: Garmin has ended it at their end too. They can link again once Garmin
raises the limit.

### The volume swap

A machine keeps its volume, so a restore puts a new machine on a new volume.
Stop the old machine first so the two never both take sign-ins.

```sh
APP=garmin-mcp-1b4387
fly volumes list -a $APP                       # the live garmin_data volume
fly volumes snapshots list <volume-id>         # pick a snapshot by its time
fly volumes create garmin_data -a $APP --region lhr --size 1 \
  --snapshot-id <snapshot-id>                  # or no --snapshot-id: empty, for Litestream
fly machine list -a $APP                       # the live machine's id
fly machine stop <machine-id> -a $APP
fly machine clone <machine-id> -a $APP --attach-volume <new-volume-id>:/data
```

Check the new machine serves (`https://garmin-mcp-1b4387.fly.dev/` and a
question in Claude), then `fly machine destroy <old-machine-id> -a $APP`.
Keep the old volume for a week before `fly volumes destroy`.

This has not been rehearsed end to end. The drill proves a snapshot becomes a
volume and a copy of the database opens with the key; it doesn't swap the
live machine.

## The drill

Run the **Restore drill** workflow by hand once a month, and after any change
to how the server stores or backs up its data. Read-only on the live server:

- copies the live database aside and checks it (integrity, counts, and that
  the key opens the newest sessions), then deletes the copy;
- with Litestream set up, restores the off-site copy and checks it the same
  way, and how far behind live it is;
- makes a volume from the newest snapshot, checks it comes up, and deletes it.

Nothing in it talks to Garmin, so no stored session is used or refreshed. It
fails if any check does.

## Never

- Set a new `GARMIN_MCP_SECRET`.
- Delete the `garmin_data` volume, or restore over a working database without
  a fresh snapshot first.
- End someone else's official link, or end your own while Garmin's limit is
  full unless it's deliberate.
- Run anything against a restored copy that refreshes Garmin sessions while
  the live server is running: Garmin would replace the live tokens.
