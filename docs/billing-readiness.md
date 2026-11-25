# Billing readiness

Notes for before Daash charges anyone (Nov 25). No code yet. The aim: taking
money must never make the service less reliable for the people paying.

## 1. Stripe webhooks are idempotent

Stripe delivers each event at least once, sometimes several times, sometimes
out of order, and retries for up to three days when we don't answer 2xx.

- Keep a `billing_events` table: `stripe_event_id TEXT PRIMARY KEY`,
  `type`, `received_at`, `processed_at`, `error`. Insert the event id first,
  inside the same transaction as the change it makes; if the insert hits the
  primary key, it was already handled: answer 200 and do nothing.
- Verify the signature (`Stripe-Signature`, the endpoint's signing secret as
  a Fly secret, `STRIPE_WEBHOOK_SECRET`) before reading anything else.
- Never trust the order. For subscription state, read the subscription from
  the event (or fetch it from Stripe) and store its `status` and
  `current_period_end` with the event's `created` time; ignore an event older
  than the state already stored.
- Answer fast. Store the event and queue a job (jobs.py, type
  `stripe_event`) for anything slow, such as an email; the worker runs it.
  A job must be safe to run twice, like every other job.
- Keep the raw events 30 days for debugging, then delete them.

## 2. Never lock out a paying person because of us

Daash already has bad days (15-25 Nov: about 20 freezes). A person who pays
must not lose access because our server, our webhook, or Stripe had one.

- **Default to access while billing status is unknown.** If the billing row is
  missing, stale, or the check itself fails (database locked, Stripe down),
  let the call through and log it. Only a known, current "not paying" state
  limits anyone.
- **Grace period.** When a payment fails (`invoice.payment_failed`,
  subscription `past_due`), keep full access for 7 days while Stripe retries
  the card (Smart Retries), and say so once, kindly, in the app and by email.
  Limit only after Stripe gives up (`unpaid` or `canceled`).
- **Our downtime extends the grace.** If the server was down for a
  meaningful share of someone's grace period (server_heartbeat gaps), extend
  it by that much.
- **A missed webhook can't lock anyone out.** A daily job reconciles every
  subscription with Stripe's API and fixes what the webhooks missed, in the
  person's favour when in doubt.
- **Limit, don't delete.** Lapsed means read-only or a few calls a day, never
  deleting their Garmin session, plans or history. Paying again restores
  everything at once.
- What "limited" means has to be decided and shown in one place, and the
  chat tools have to say it in words the AI can pass on ("Your Daash
  subscription has ended; renew at …"), not fail with a bare error.

## 3. Deletion and export (UK GDPR)

**Deletion (right to erasure).** Today, "Disconnect" on the website and
"Delete account" in the app (`hosted.forget_person`) delete: the stored
Garmin session, Garmin's official link (Garmin is told too), crew
membership, race prompts, phone notification subscriptions, usage counts,
every Claude, ChatGPT and app sign-in token, and since Nov 25 the cached
Garmin answers (`garmin_cache`). Still to add before charging:

- the person in PostHog: delete the person and their events through
  PostHog's API (`DELETE /api/projects/<id>/persons/<id>/?delete_events=true`)
  by their distinct id, as a job, since PostHog may be slow;
- emails sent and weekly-email records keyed by their email fingerprint
  (`email_sends`, `weekly_skips`), and any `notices` rows for them;
- jobs still queued for them (`jobs` payloads naming their Garmin user id);
- Stripe: cancel the subscription and, on request, delete the Stripe customer
  (keeping only what the law requires for invoices: HMRC asks for six years
  of the invoice itself, not the training data);
- off-site backups: say in the privacy policy that deleted data leaves
  Litestream's copy within its 30-day retention, and the daily volume
  snapshots within 30 days.

One job, `delete_person`, should do all of it and record that it did
(time, and which parts), without keeping anything about the person.

**Export (right of access and portability).** Add `GET /app/api/you/export`
(signed in, rate-limited): a JSON file with what Daash holds about them:
account (masked email, when connected, which route), plans and
plan choices, crews, notification settings, weekly-email history, usage
counts, and their billing history. Not the Garmin data itself, which Garmin
Connect exports; link to that, as the app already does. Answer within a
month by law; the endpoint answers at once.

## 4. Before the first payment

- Uptime on the System Health dashboard at 99.5% or better for two weeks.
- Status page live and linked from the site and the support page.
- Terms and privacy policy updated for payments, refunds and the above.
- A refund rule decided (for example: pro-rata on request within 14 days,
  the UK cooling-off period for digital services).
