# Reaction Dynamics — production hardening

**Date:** 2026-08-15
**Status:** design approved, not yet implemented
**Scope:** turn the hack-night demo into a self-hostable tool a team can run for months

---

## 1. Problem

Reaction Dynamics currently works, and the mock-purge audit (2026-08-15) confirmed it
fabricates nothing: every number traces to real captured data, and no script anywhere
posts a synthetic reaction. But it is a demo, and it fails in the one way this particular
product cannot afford to fail.

Per-reaction timing exists only in the live Slack `reaction_added` event. If the listener
is not running at the moment someone reacts, that timing is gone permanently — no export,
no API call, no backfill can reconstruct it. The data is uniquely irreplaceable.

Today, nothing records when the listener was not running. A process that is down from
09:00 to 17:00 leaves no trace of its absence. A week later the analysis reads "a quiet
morning" when the truth is "we were not listening." The tool silently substitutes an
optimistic answer for a missing one.

That is the defect this work exists to fix.

## 2. Organizing principle

**Absence must be loud.**

The audit's rule and the product's core requirement are the same rule. A system that
reports "the second check did not run" is trustworthy; one that quietly returns the
optimistic answer is not.

Applied here: the tool must know, record, and report every window in which it was not
watching. That capability — not storage, not throughput — is what distinguishes this
from a batch analytics tool, and it is the headline of the production version.

## 3. Non-goals

- **Scalability work.** Measured from the live capture: 8 reactions/hour, busiest 60
  seconds had 3, at 416 bytes/event. Extrapolated 100× to a large workspace that is
  0.22 writes/sec — roughly five orders of magnitude below SQLite's write ceiling.
  Reaction events are human-paced; the ceiling is how fast people click. No plausible
  Slack workspace out-writes SQLite. Scaling effort here would be solving a problem the
  workload does not have.
- **Hosted / multi-tenant SaaS.** Explicitly out of scope. No OAuth install flow, no
  billing, no per-workspace provisioning.
- **Async rewrite of the listener.** The synchronous `builtin` Socket Mode client was
  chosen deliberately (`seed/listen_slack.py:161`) and is not the problem.
- **Unrelated refactoring.** The GitHub corpus path, the Cognee/Qdrant ingest, and the
  graph export are left as they are except where the audit findings touch them.

## 4. Architecture

Three new modules, one rewritten entry point, one test package. The existing
dependency-free classifier (`seed/shapes.py`) and corpus schema (`seed/schema.py`) are
unchanged in behaviour — they gain tests, not edits.

```
seed/store.py      NEW  SQLite persistence + gap ledger + connection seam
seed/capture.py    NEW  supervised daemon; imports the handlers from listen_slack.py
seed/listen_slack.py    handlers extracted into importable functions; its own main()
                        stays, so the existing standalone JSONL path keeps working
seed/live_server.py     reads the DB instead of re-scanning JSONL; honest staleness
tests/                  NEW  pytest suite
pyproject.toml     NEW  dev-only dependencies; runtime stays stdlib
```

Runtime dependency rule, preserved: `python3.12 seed/shapes.py --dialects --twins` must
continue to run on a freshly cloned machine with zero installs and no network. pytest is
a dev extra only.

## 5. Component: `seed/store.py`

### Schema

```sql
CREATE TABLE events (
  event_id   TEXT PRIMARY KEY,   -- sha1(kind|channel|message_ts|user_id|emoji|event_ts)
  kind       TEXT NOT NULL,      -- reaction_added | reaction_removed | message
  channel    TEXT,
  message_ts TEXT,
  user_id    TEXT,
  user       TEXT,
  emoji      TEXT,
  event_ts   REAL,               -- THE signal; sorts arrival order
  ts_iso     TEXT,
  raw        TEXT                -- full original payload, JSON
);
CREATE INDEX ix_events_msg  ON events(channel, message_ts, event_ts);
CREATE INDEX ix_events_time ON events(event_ts);

CREATE TABLE messages (
  channel TEXT, ts TEXT, user_id TEXT, user TEXT, text TEXT, ts_iso TEXT,
  PRIMARY KEY (channel, ts)
);

CREATE TABLE capture_gaps (
  id         INTEGER PRIMARY KEY,
  channel    TEXT,
  started_at REAL NOT NULL,      -- last moment we know we were listening
  ended_at   REAL,               -- NULL while the gap is still open
  reason     TEXT NOT NULL       -- cold_start | watchdog_silence | disconnected | clean_shutdown | crash
);

CREATE TABLE heartbeat (
  channel TEXT PRIMARY KEY,
  last_seen_at REAL NOT NULL,    -- last socket activity, not last reaction
  events_total INTEGER NOT NULL DEFAULT 0
);
```

### Design decisions

- **WAL mode.** Concurrent reader (dashboard) never blocks the writer (capture).
- **`INSERT OR IGNORE` on `event_id`.** Capture becomes idempotent. Socket Mode replays
  unacked events and reconnects can redeliver; a deterministic hash of the event's
  identifying fields makes double-delivery a no-op instead of a double-count.
- **`raw` retained in full.** The derived columns are a convenience; the original payload
  is kept so a future schema change can re-derive rather than re-capture. Re-capture is
  impossible, so nothing may be discarded at write time.
- **Connection seam.** libSQL is SQLite-compatible, so the storage choice is not a fork
  in the road:

```python
def connect(url=None):
    """A filesystem path uses stdlib sqlite3.
    A libsql:// URL uses a Turso embedded replica: local-speed writes,
    async replication for off-machine durability."""
    if url and url.startswith("libsql://"):
        import libsql                       # opt-in; never imported by default
        return libsql.connect(LOCAL_REPLICA, sync_url=url,
                              auth_token=os.environ["TURSO_TOKEN"])
    return sqlite3.connect(url or DEFAULT_PATH)
```

  Default: zero dependencies, clone-and-go. Off-machine durability is one env var away.
  Schema, queries, and tests are identical under both. Turso's embedded-replica model is
  the right shape here specifically because writes stay local — a network stall must never
  block capture, and a stall is most likely during exactly the traffic bursts that matter.
  Before adopting it, verify the current Python client's maturity and free-tier terms.

### Gap ledger API

```python
open_gap(conn, channel, started_at, reason) -> int
close_gap(conn, gap_id, ended_at)
open_gaps(conn) -> list          # gaps never closed = crash windows
gaps_overlapping(conn, t0, t1)   # for analysis-time honesty
```

### Migration

`migrate_jsonl(conn, path)` imports the existing `seed/live_events.jsonl` so the hack-night
capture survives. Idempotent by construction (same `INSERT OR IGNORE`), so it is safe to
re-run.

## 6. Component: `seed/capture.py`

### The bug being fixed

`seed/listen_slack.py:180-186`:

```python
client.connect()
while True:
    time.sleep(1)
```

`slack_sdk` reconnects internally, but if that client thread dies the main loop sleeps
forever: process alive, exit code 0, tally frozen, capturing nothing. A silently dead
listener is indistinguishable from a quiet Slack. This is the same defect class as the
dashboard's swallowed `.catch`, but here it destroys irreplaceable data.

### Approaches considered

1. **External supervision only** (systemd/launchd restart on crash). Simple, but does not
   catch the failure that matters — the process is not crashing, it is idle. Insufficient
   as the primary mechanism.
2. **Full async rewrite** on the aiohttp client. Cleaner concurrency, but destabilizes the
   one component that must not be destabilized, and reverses a deliberate decision.
   Rejected.
3. **Liveness-supervised sync loop.** Keep the builtin client; replace the sleep loop with
   a watchdog that treats silence as failure. **Chosen.**

### Watchdog

> **Corrected 2026-08-15, after live testing.** This section originally asserted that
> Socket Mode pings arrive as events, so any healthy connection shows traffic. **That is
> false in this SDK**, and building on it produced a defect that all tests passed over:
> the watchdog fabricated a 92-second gap on a socket that was never down. Keepalives are
> WebSocket **PING/PONG control frames**, handled inside
> `slack_sdk/socket_mode/builtin/connection.py` at the protocol layer; they are not JSON
> data frames and reach *no* listener list. Registering the sibling `message_listeners`
> looks like the fix and is not — that list sees only text frames such as `hello` and
> `disconnect`, which on an idle channel are as rare as events. See §5 of
> `docs/HANDOFF-durable-capture.md`.

*Silence is a failure signal*, distinct from *no reactions* — but it must be measured from
two independent sources, and the most recent wins:

1. **A Slack event**, which calls `dog.beat()` and stamps `heartbeat.last_seen_at`.
2. **The SDK's ping/pong stamp**, `client.current_session.last_ping_pong_time`, which the
   watchdog **polls** via a `probe` callable. It cannot call us; we must read it.

Events alone are insufficient: a quiet channel produces none for hours while its socket is
healthy. The probe reads the client through a mutable `state` dict rather than a
closed-over name, because `client` is rebound on every reconnect. It returns `None` — never
a guess — when the reading is unavailable, falling back to beat-only behaviour; a probe
that failed *open* would silently disarm the watchdog, which is worse than having none.
- If `now - last_seen_at > SILENCE_LIMIT` (default 90s, configurable): tear down the
  client, open a `capture_gaps` row covering the dark window with reason
  `watchdog_silence`, reconnect with exponential backoff, close the gap on success.
- **Explicit disconnect**, the fast path: silence is a *timeout* and cannot speak until the
  full limit has elapsed. When `client.is_connected()` already reports `False`, that is
  known at once, and waiting 90s to agree with it is 90s of avoidable dark time. Same
  teardown and reconnect, reason `disconnected`. Guarded by a `CONNECT_GRACE` window
  (default 5s) after each connect attempt, because the SDK also reports `False` mid-
  handshake — tripping on that would churn a gap per second on a socket coming up normally.
  Unknown is not down: no `is_connected`, or one that raises, degrades to silence-only
  detection rather than reconnecting in a loop.
- **Cold start:** on boot, read `heartbeat.last_seen_at`. If it exists, the window between
  then and now was unwatched — write a gap with reason `cold_start`. If the previous run
  left an open gap, it crashed; reason is corrected to `crash`.
- **Clean shutdown:** SIGINT/SIGTERM closes the current gap and stamps
  `heartbeat.last_seen_at`, so a planned restart produces an accurate short gap rather
  than a silent one.

Net effect: **every window the tool cannot positively account for becomes a record.**

### Other capture-path fixes

- **fsync.** Line buffering flushes to the OS, not to disk. SQLite WAL with
  `synchronous=NORMAL` plus periodic checkpoint gives durability without a per-event
  fsync stall.
- **User-name resolution off the capture path.** `name_of()` currently calls
  `web.users_info()` inline in the event handler. It runs after the ack, but it is still
  a synchronous HTTP round-trip inside the handler — worst case during the cascades the
  product exists to measure. Move to a background resolver thread with a DB-backed cache;
  write `user_id` immediately and backfill `user`.
- **Counters restored from the DB** on start, so the tally reflects the capture, not the
  process.

### Deployment

Ship documented `launchd` (macOS) and `systemd` (Linux) units for crash-restart, layered
*under* the watchdog. External supervision handles the process dying; the watchdog handles
the process living but not working.

## 7. Component: tests

pytest, dev-dependency only. `pyproject.toml` declares a `[dev]` extra; runtime imports
nothing new.

| Area | What is actually asserted |
|---|---|
| `shapes.py` classifier | Uniform arrivals never classify as `cascade`. Fewer than 4 timed reactions always refuses (`forming`), never guesses. KS statistic matches known-answer fixtures. Burstiness reproduces Goh–Barabási values for regular (−1), Poisson (~0), and bursty (→+1) sequences. |
| `store.py` | Inserting the same event 100× yields one row. Gap ledger opens and closes correctly across cold start, watchdog trip, clean shutdown, and crash. JSONL migration round-trips and is re-runnable. |
| Watchdog | With an injected fake clock and a stub client: silence past the threshold triggers reconnect **and** records a gap of the correct span. The stub lives in `tests/`, injected — test-only by construction. |
| `ask.py` routing | Locks the keyword router's behaviour so the documented tool-selection claims stay true. |

**Stated in the suite's own output and in the README:** no test contacts Slack, Cognee, or
Qdrant. A green run never implies the live path works. Per the audit's rule, "all tests
pass" and "no real provider was contacted" are both true and must appear together.

## 8. Audit findings, folded in

From the mock-purge of 2026-08-15:

1. `seed/ask.py:7` and `README.md:98` claim the LLM routes tool selection. It does not —
   `keyword_route()` at `ask.py:206` is unconditional and the LLM only writes prose.
   Correct both claims.
2. `seed/live_server.py:255` — `.catch(function(){})` leaves stale numbers under a pulsing
   LIVE indicator when the server dies. Replace with a staleness banner. With the DB in
   place, the dashboard additionally surfaces open capture gaps.
3. `dashboard.html:478` / `site/index.html:478` — hardcoded `✅ running` reads present
   tense on a public site. Date-stamp it as past.
4. `seed/ingest_cognee.py:196` — `except Exception: continue` reports search failures as
   "nothing matched," pointing at the wrong diagnosis. Collect and report the errors.

## 9. Logo

Two deliverables:

- A hand-authored SVG mark: legible at favicon size, theme-aware, no dependency, committed
  to `docs/assets/`.
- A written image-generator prompt for Midjourney/Ideogram/DALL·E, since no image tool is
  available in-session.

Concept direction: four reaction dots arriving along a timeline with visibly unequal
spacing — cascade versus trickle expressed as *rhythm*, which is literally the quantity the
product measures.

## 10. LinkedIn post

Written last, from what is true once the build lands, drawing on the verified module
library in `docs/LINKEDIN-POST.md`.

Candidate hook — the gap ledger, which is the least obvious and most distinctive result:

> I built a tool that measures my team's emoji. The most important thing it does is admit
> when it wasn't looking.

Constraint carried over from the audit: every claim must survive a reader clicking through
to the repo.

## 11. Build order

The first four items are the slice that earns the word "production" on their own:

1. `store.py` + schema + JSONL migration
2. `capture.py` watchdog + gap ledger
3. Classifier and store tests
4. Dashboard reads the DB; staleness and open gaps shown honestly
5. Audit findings 1, 3, 4
6. Deployment units and README rewrite
7. Logo
8. LinkedIn post

## 12. Risks

| Risk | Mitigation |
|---|---|
| Touching the listener costs live capture | `capture.py` is additive; `listen_slack.py` keeps working unchanged until the new path is tested |
| Watchdog false positives cause churn | Silence limit is configurable and defaults well above Socket Mode's ping interval; every trip is recorded, so false positives are visible rather than silent |
| Gap ledger over-reports | A recorded gap that turns out to be spurious is a conservative error — it understates confidence rather than fabricating data |
| Scope grows past one session | Build order is ranked; items 1–4 stand alone as a shippable increment |
