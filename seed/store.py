"""Durable capture store.

Per-reaction timing exists only in the live Slack event and cannot be
re-captured. Everything here follows from that:

  - writes are idempotent, so a reconnect or replay cannot double-count;
  - the full original payload is kept, so a later schema change can re-derive
    instead of asking for data that no longer exists;
  - every window the process was not listening becomes a row in capture_gaps
    (see the gap ledger below), because a gap that looks like silence is the
    one failure this product cannot afford.

Stdlib sqlite3 by default. libSQL/Turso is opt-in and changes nothing else:
it is SQLite-compatible, so the schema, the queries and the tests are identical.
"""

import hashlib
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "capture.db")
LOCAL_REPLICA = os.path.join(HERE, "capture.replica.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id   TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  channel    TEXT,
  message_ts TEXT,
  user_id    TEXT,
  user       TEXT,
  emoji      TEXT,
  event_ts   REAL,
  ts_iso     TEXT,
  raw        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_msg  ON events(channel, message_ts, event_ts);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(event_ts);

CREATE TABLE IF NOT EXISTS messages (
  channel TEXT NOT NULL,
  ts      TEXT NOT NULL,
  user_id TEXT,
  user    TEXT,
  text    TEXT,
  ts_iso  TEXT,
  PRIMARY KEY (channel, ts)
);

CREATE TABLE IF NOT EXISTS capture_gaps (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  channel    TEXT,
  started_at REAL NOT NULL,
  ended_at   REAL,
  reason     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_gaps_open ON capture_gaps(ended_at);

CREATE TABLE IF NOT EXISTS heartbeat (
  channel      TEXT PRIMARY KEY,
  last_seen_at REAL NOT NULL,
  events_total INTEGER NOT NULL DEFAULT 0
);
"""


def connect(url=None):
    """A filesystem path (or ':memory:') uses stdlib sqlite3.

    A libsql:// URL uses a Turso embedded replica: writes land in a local
    SQLite file at local speed and replicate asynchronously. That ordering
    matters here -- a network stall must never block capture, and a stall is
    most likely during exactly the bursts worth measuring.
    """
    if url and url.startswith("libsql://"):
        import libsql  # opt-in; never imported on the default path
        conn = libsql.connect(LOCAL_REPLICA, sync_url=url,
                              auth_token=os.environ["TURSO_TOKEN"])
    else:
        conn = sqlite3.connect(url or DEFAULT_PATH, isolation_level=None)
        if url != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init(conn):
    conn.executescript(SCHEMA)


def event_id(kind, channel, message_ts, user_id, emoji, event_ts):
    """Deterministic identity for one captured event.

    These six fields are what make an event unique: the same person putting the
    same emoji on the same message at the same instant IS the same event, however
    many times Slack delivers it.
    """
    parts = (kind, channel, message_ts, user_id, emoji, event_ts)
    return hashlib.sha1("|".join(str(p or "") for p in parts).encode()).hexdigest()


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def record_event(conn, kind, payload):
    """Insert one event. Returns True if newly stored, False if already known."""
    eid = event_id(kind, payload.get("channel"), payload.get("message_ts"),
                   payload.get("user_id"), payload.get("emoji"),
                   payload.get("event_ts"))
    cur = conn.execute(
        "INSERT OR IGNORE INTO events "
        "(event_id, kind, channel, message_ts, user_id, user, emoji, "
        " event_ts, ts_iso, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (eid, kind, payload.get("channel"), payload.get("message_ts"),
         payload.get("user_id"), payload.get("user"), payload.get("emoji"),
         _as_float(payload.get("event_ts")), payload.get("ts_iso"),
         json.dumps(payload)))
    return cur.rowcount > 0


def record_message(conn, payload):
    """Insert one message. Returns True if newly stored, False if already known."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO messages "
        "(channel, ts, user_id, user, text, ts_iso) VALUES (?,?,?,?,?,?)",
        (payload.get("channel"), payload.get("ts"), payload.get("user_id"),
         payload.get("user"), payload.get("text"), payload.get("ts_iso")))
    return cur.rowcount > 0


def count_events(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]


# ------------------------------------------------------------- the gap ledger
#
# The product's central honesty mechanism. Reaction timing that was not captured
# is gone forever, so the tool must never let "we were not listening" look like
# "nobody reacted". Every window we cannot positively account for gets a row.


def open_gap(conn, channel, started_at, reason):
    """Start recording a window in which we were not capturing.

    reason: cold_start | watchdog_silence | disconnected | crash | clean_shutdown
    """
    cur = conn.execute(
        "INSERT INTO capture_gaps (channel, started_at, ended_at, reason) "
        "VALUES (?,?,NULL,?)", (channel, started_at, reason))
    return cur.lastrowid


def close_gap(conn, gap_id, ended_at):
    """Mark a gap as over. Coverage resumes here."""
    conn.execute("UPDATE capture_gaps SET ended_at=? WHERE id=?",
                 (ended_at, gap_id))


def open_gaps(conn):
    """Gaps with no end. On startup these are the previous run's crash windows."""
    return conn.execute(
        "SELECT * FROM capture_gaps WHERE ended_at IS NULL "
        "ORDER BY started_at").fetchall()


def gaps_overlapping(conn, t0, t1):
    """Every gap intersecting [t0, t1]. An unclosed gap is treated as ongoing,
    so it still overlaps any window after it began."""
    return conn.execute(
        "SELECT * FROM capture_gaps "
        "WHERE started_at <= ? AND (ended_at IS NULL OR ended_at >= ?) "
        "ORDER BY started_at", (t1, t0)).fetchall()


def total_dark_seconds(conn):
    """Total recorded time not capturing. Closed gaps only -- an open gap has no
    measurable length yet, and guessing one would be the exact error this
    ledger exists to prevent."""
    row = conn.execute(
        "SELECT COALESCE(SUM(ended_at - started_at), 0.0) AS s "
        "FROM capture_gaps WHERE ended_at IS NOT NULL").fetchone()
    return float(row["s"])


def touch_heartbeat(conn, channel, at, inc=1):
    """Stamp the last moment this channel was known to be covered.

    Called when a Slack event arrives, and again on reconnect (inc=0). NOT on
    pings: Socket Mode keepalives are WebSocket control frames and never reach
    application code, so an idle channel stamps nothing for hours while its
    socket is fine. That is why the watchdog does not rely on this row alone --
    it also polls the SDK's ping/pong time. What this row is for is the *next*
    boot: it marks where coverage stopped, so reconcile_startup can record the
    dark window instead of guessing at one."""
    conn.execute(
        "INSERT INTO heartbeat (channel, last_seen_at, events_total) "
        "VALUES (?,?,?) "
        "ON CONFLICT(channel) DO UPDATE SET "
        "  last_seen_at=excluded.last_seen_at, "
        "  events_total=heartbeat.events_total + ?",
        (channel, at, inc, inc))


def last_seen(conn, channel):
    """When we last knew we were listening, or None if we never have been."""
    row = conn.execute("SELECT last_seen_at FROM heartbeat WHERE channel=?",
                       (channel,)).fetchone()
    return float(row["last_seen_at"]) if row else None


# ---------------------------------------------------------------- JSONL import


def migrate_jsonl(conn, path):
    """Import an existing live_events.jsonl into the store.

    Idempotent, because record_event is. Tolerates a torn final line, which is
    what a killed listener leaves behind -- the preceding lines are real captured
    timing and must not be discarded over a truncated one.
    """
    out = {"events": 0, "messages": 0, "skipped": 0, "malformed": 0}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                out["malformed"] += 1
                continue
            kind = e.get("kind")
            if kind == "message":
                if record_message(conn, e):
                    out["messages"] += 1
                else:
                    out["skipped"] += 1
            elif kind in ("reaction_added", "reaction_removed"):
                if record_event(conn, kind, e):
                    out["events"] += 1
                else:
                    out["skipped"] += 1
    return out
