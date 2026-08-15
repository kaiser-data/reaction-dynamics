# Durable Capture Implementation Plan

> ⚠️ **SUPERSEDED — this plan was executed on 2026-08-15. Read
> `docs/HANDOFF-durable-capture.md` first.** This document is preserved as the record of
> what was *intended*; several of its assertions turned out to be wrong on contact with a
> running system, and the code below is not what shipped. In particular, every place this
> plan says Socket Mode pings arrive as events or reach a listener is **false** — that
> premise caused a defect in which the watchdog fabricated a gap for a socket that was
> never down. Do not copy code out of this file without checking it against `seed/`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Reaction Dynamics record every window in which it was *not* capturing, so a gap in coverage can never be misread as a quiet Slack room.

**Architecture:** A stdlib-SQLite store (`seed/store.py`) replaces append-only JSONL as the durable record, with idempotent writes keyed on a deterministic event hash. A supervised daemon (`seed/capture.py`) wraps the existing Socket Mode handlers with a watchdog that treats *socket silence* as failure — reconnecting and writing a `capture_gaps` row for every dark window. The live dashboard reads the DB and shows staleness and open gaps instead of silently freezing.

**Tech Stack:** Python 3.12.3, stdlib `sqlite3` (3.45.1, WAL mode), `slack_sdk` 3.43.0 (already installed), pytest (dev-only, to be installed).

## Global Constraints

- **Runtime stays stdlib-only.** `python3.12 seed/shapes.py --dialects --twins` must keep running on a fresh clone with zero installs and no network. pytest is a dev extra; no runtime module may import it.
- **Python 3.12.3**, SQLite 3.45.1. Verified present in `.venv`.
- **`.venv` is a symlink** to `/Users/marty/claude-projects/hackathon/cognee-qdrant-slack-test/.venv`. Installing pytest there also affects that sibling project. This is acceptable (pytest is additive) but must be stated, not discovered.
- **Never discard a captured payload.** The `raw` column keeps the full original event so a later schema change can re-derive rather than re-capture. Re-capture is impossible.
- **All writes idempotent.** Socket Mode replays unacked events; reconnects redeliver. Double-delivery must be a no-op, never a double-count.
- **Absence is recorded, never inferred.** Any window the process cannot positively account for becomes a `capture_gaps` row.
- **Do not commit unless the operator asks.** Steps below show the commit command for when they do; do not run it unprompted.
- **`seed/listen_slack.py` keeps a working `main()`** throughout. The existing standalone JSONL path must not break while `capture.py` is built.

---

### Task 1: Test harness and the first verified classifier test

Establishes pytest and locks the single most important behaviour of the classifier: it refuses to name a shape when it has too little evidence.

**Files:**
- Create: `pyproject.toml`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/test_shapes.py`

**Interfaces:**
- Consumes: `seed/shapes.py::classify(reactions, msg_ts=None, window_h=WINDOW_H)` — existing, unmodified.
- Produces: `tests/conftest.py::rx(offsets, emoji="+1", base=ANCHOR)` — builds a reaction list at given second-offsets. `tests/conftest.py::ANCHOR` — the fixed message timestamp string every test anchors to. Later test tasks import both.

- [ ] **Step 1: Install pytest into the venv**

```bash
cd /Users/marty/claude-projects/hackathon/cognee-slack/hackathon
.venv/bin/python -m pip install pytest
```

Expected: `Successfully installed pytest-...`

- [ ] **Step 2: Create `pyproject.toml`**

Declares pytest as a dev-only extra and puts `seed/` on the import path so tests can `import shapes` the same way the scripts do.

```toml
[project]
name = "reaction-dynamics"
version = "0.2.0"
description = "Turns Slack reaction timing into team clarity"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]
live = ["slack_sdk>=3.43"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["seed"]
addopts = "-v"
```

- [ ] **Step 3: Create `tests/__init__.py`**

Empty file.

```bash
touch tests/__init__.py
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
"""Shared fixtures. Every timing test anchors to one fixed instant so that
offsets in a test read as 'seconds after the message was posted'."""

from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
ANCHOR = BASE.isoformat()


def rx(offsets, emoji="+1", base=BASE):
    """Reactions at the given second-offsets after `base`.

    rx([0, 600, 1200]) -> three '+1' reactions, ten minutes apart.
    """
    return [
        {"user": f"u{i}", "emoji": emoji,
         "ts": (base + timedelta(seconds=s)).isoformat()}
        for i, s in enumerate(offsets)
    ]
```

- [ ] **Step 5: Write the failing test**

`tests/test_shapes.py`:

```python
"""Characterization tests for the shape classifier.

Every expected value here was verified against the live classifier before being
written down. These lock current behaviour so a refactor cannot quietly change
what 'cascade' means.

No test in this file touches Slack, Cognee, Qdrant, or the network.
"""

from conftest import ANCHOR, rx
from shapes import MIN_REACTIONS, classify


def test_refuses_below_minimum_reactions():
    """The single most important behaviour: too little evidence -> no answer.

    Three timed reactions cannot support a shape, so classify returns None
    rather than guessing. A tool that invents a shape from n=3 is worse than
    one that stays quiet.
    """
    assert MIN_REACTIONS == 4
    assert classify(rx([0, 600, 1200]), ANCHOR) is None
```

- [ ] **Step 6: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_shapes.py -v
```

Expected at this point: **PASS**, not fail — `classify` already behaves correctly. This test is a characterization test, not a red-green cycle: its job is to lock existing behaviour so it cannot regress. If it *fails*, stop and investigate, because the classifier does not do what the README claims.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tests/
git commit -m "test: add pytest harness and lock classifier evidence threshold"
```

---

### Task 2: Lock the four shapes and the burstiness measure

**Files:**
- Modify: `tests/test_shapes.py` (append)

**Interfaces:**
- Consumes: `conftest.rx`, `conftest.ANCHOR`, `shapes.classify`, `shapes.burstiness`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Append the shape tests**

All expected values below were confirmed by running the classifier directly. Note `stall-burst`: it keys off arrival spread **within the reaction span**, not latency from the message — reactions bunched late in their own span, not merely late overall.

```python
def test_uniform_arrivals_classify_as_trickle():
    """Evenly spaced arrivals are indistinguishable from independent decisions.
    KS statistic is 0 here, well under the 1.36/sqrt(n) critical value."""
    c = classify(rx([0, 600, 1200, 1800, 2400, 3000]), ANCHOR)
    assert c["shape"] == "trickle"


def test_front_loaded_arrivals_classify_as_cascade():
    """Five reactions in five seconds, then a straggler two hours later.
    mean_u sits far below 0.25, which is the cascade signature."""
    c = classify(rx([0, 1, 2, 3, 4, 7200]), ANCHOR)
    assert c["shape"] == "cascade"


def test_simultaneous_arrivals_classify_as_cascade():
    """Zero span is the degenerate cascade: everyone at the same instant."""
    c = classify(rx([0, 0, 0, 0]), ANCHOR)
    assert c["shape"] == "cascade"


def test_late_cluster_classifies_as_stall_burst():
    """One early reaction, a long silence, then everyone at once.
    mean_u = 0.79, above the 0.75 stall-burst threshold."""
    c = classify(rx([0, 7000, 7100, 7150, 7199]), ANCHOR)
    assert c["shape"] == "stall-burst"
    assert c["mean_u"] > 0.75


def test_opposed_emoji_classify_as_split_regardless_of_timing():
    """Disagreement is a content property, orthogonal to arrival timing.
    Four +1 against two -1: the minority is 33%, over the 20% threshold."""
    c = classify(rx([0, 600, 1200, 1800]) + rx([300, 900], emoji="-1"), ANCHOR)
    assert c["shape"] == "split"
    assert c["split"] == {"for": 4, "against": 2}


def test_split_preserves_the_underlying_timing_shape():
    """`split` overrides `shape`, but the timing read must remain inspectable."""
    c = classify(rx([0, 600, 1200, 1800]) + rx([300, 900], emoji="-1"), ANCHOR)
    assert c["timing_shape"] in {"cascade", "trickle", "stall-burst", "mixed"}
    assert c["shape"] != c["timing_shape"]
```

- [ ] **Step 2: Append the burstiness tests**

Goh & Barabási (2008) define `B = (sd - mean) / (sd + mean)`, bounded in `[-1, +1]`.

```python
from shapes import burstiness


def test_burstiness_of_perfectly_regular_gaps_is_minus_one():
    """Zero variance -> sd is 0 -> B = -1. The published lower bound."""
    assert burstiness([10, 10, 10, 10]) == -1.0


def test_burstiness_rises_with_irregularity():
    """A long gap among short ones is more bursty than uniform spacing."""
    assert burstiness([1, 1, 1, 100]) > burstiness([10, 10, 10, 10])


def test_burstiness_is_bounded():
    """B must stay inside [-1, 1] for any gap sequence."""
    for gaps in ([1, 1000000], [5, 5, 5], [1, 2, 3, 4, 5]):
        assert -1.0 <= burstiness(gaps) <= 1.0


def test_burstiness_needs_two_gaps():
    """Fewer than two gaps carries no spacing information; returns 0.0."""
    assert burstiness([42]) == 0.0
    assert burstiness([]) == 0.0
```

- [ ] **Step 3: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_shapes.py -v
```

Expected: all PASS (11 tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_shapes.py
git commit -m "test: lock the four arrival shapes and the burstiness measure"
```

---

### Task 3: `store.py` — schema, connection seam, idempotent event writes

**Files:**
- Create: `seed/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `store.connect(url=None) -> sqlite3.Connection`
  - `store.init(conn) -> None`
  - `store.event_id(kind, channel, message_ts, user_id, emoji, event_ts) -> str`
  - `store.record_event(conn, kind, payload) -> bool` (True if newly inserted, False if already present)
  - `store.record_message(conn, payload) -> bool`
  - `store.count_events(conn) -> int`
  - Tasks 4, 5, 7 and 8 all build on these.

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:

```python
"""Store tests. Every test runs against an in-memory database — no file, no
network, no Slack. A green run here says nothing about the live capture path."""

import pytest

import store


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init(c)
    yield c
    c.close()


REACTION = {
    "event_ts": "1786726570.000100",
    "ts_iso": "2026-08-14T16:56:10+00:00",
    "user": "Martin",
    "user_id": "U123",
    "emoji": "+1",
    "channel": "C0BQ7FGF82H",
    "message_ts": "1786726564.102969",
}


def test_records_a_reaction(conn):
    assert store.record_event(conn, "reaction_added", REACTION) is True
    assert store.count_events(conn) == 1


def test_reinserting_the_same_event_is_a_noop(conn):
    """Socket Mode replays unacked events and reconnects redeliver. Capturing
    the same reaction a hundred times must still be one reaction, or every
    reconnect inflates the numbers the product reports."""
    store.record_event(conn, "reaction_added", REACTION)
    for _ in range(99):
        assert store.record_event(conn, "reaction_added", REACTION) is False
    assert store.count_events(conn) == 1


def test_different_reactors_are_different_events(conn):
    """Same message, same emoji, different person -> two rows."""
    store.record_event(conn, "reaction_added", REACTION)
    other = dict(REACTION, user_id="U999", user="Ada",
                 event_ts="1786726571.000200")
    store.record_event(conn, "reaction_added", other)
    assert store.count_events(conn) == 2


def test_raw_payload_is_preserved(conn):
    """Timing cannot be re-captured, so nothing may be dropped at write time."""
    store.record_event(conn, "reaction_added", REACTION)
    row = conn.execute("SELECT raw FROM events").fetchone()
    import json
    assert json.loads(row["raw"])["emoji"] == "+1"


def test_event_ts_is_stored_as_a_sortable_number(conn):
    """event_ts IS the signal; it must sort as a number, not a string."""
    store.record_event(conn, "reaction_added", REACTION)
    row = conn.execute("SELECT event_ts FROM events").fetchone()
    assert isinstance(row["event_ts"], float)
    assert row["event_ts"] == pytest.approx(1786726570.0001)


def test_messages_are_keyed_by_channel_and_ts(conn):
    msg = {"ts": "1786726564.102969", "ts_iso": "2026-08-14T16:56:04+00:00",
           "user": "Martin", "user_id": "U123", "text": "Tabs or spaces?",
           "channel": "C0BQ7FGF82H"}
    assert store.record_message(conn, msg) is True
    assert store.record_message(conn, msg) is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: Write `seed/store.py`**

```python
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
    matters here — a network stall must never block capture, and a stall is
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_store.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Add `capture.db` to `.gitignore`**

Captured Slack content is workspace data and must never be committed.

Append to `.gitignore`:

```
# captured Slack data -- never commit workspace content
capture.db
capture.db-wal
capture.db-shm
capture.replica.db*
```

- [ ] **Step 6: Commit**

```bash
git add seed/store.py tests/test_store.py .gitignore
git commit -m "feat: add durable SQLite capture store with idempotent writes"
```

---

### Task 4: `store.py` — the gap ledger

The feature the whole plan exists for.

**Files:**
- Modify: `seed/store.py` (append)
- Create: `tests/test_gaps.py`

**Interfaces:**
- Consumes: `store.connect`, `store.init` from Task 3.
- Produces:
  - `store.open_gap(conn, channel, started_at, reason) -> int`
  - `store.close_gap(conn, gap_id, ended_at) -> None`
  - `store.open_gaps(conn) -> list[sqlite3.Row]`
  - `store.gaps_overlapping(conn, t0, t1) -> list[sqlite3.Row]`
  - `store.touch_heartbeat(conn, channel, at, inc=1) -> None`
  - `store.last_seen(conn, channel) -> float | None`
  - `store.total_dark_seconds(conn) -> float`
  - Task 7 (watchdog) and Task 8 (dashboard) consume all of these.

- [ ] **Step 1: Write the failing test**

`tests/test_gaps.py`:

```python
"""The gap ledger: the product's answer to 'were you actually watching?'

A gap that is not recorded becomes indistinguishable from a quiet room, and a
quiet room is a finding while a dead listener is a bug. These tests exist to
keep those two apart."""

import pytest

import store

CH = "C0BQ7FGF82H"


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init(c)
    yield c
    c.close()


def test_open_gap_is_listed_as_open(conn):
    gid = store.open_gap(conn, CH, 1000.0, "cold_start")
    rows = store.open_gaps(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == gid
    assert rows[0]["reason"] == "cold_start"
    assert rows[0]["ended_at"] is None


def test_closed_gap_is_no_longer_open(conn):
    gid = store.open_gap(conn, CH, 1000.0, "watchdog_silence")
    store.close_gap(conn, gid, 1300.0)
    assert store.open_gaps(conn) == []


def test_dark_time_sums_closed_gaps(conn):
    """Five minutes dark, then three more."""
    store.close_gap(conn, store.open_gap(conn, CH, 1000.0, "cold_start"), 1300.0)
    store.close_gap(conn, store.open_gap(conn, CH, 2000.0, "crash"), 2180.0)
    assert store.total_dark_seconds(conn) == pytest.approx(480.0)


def test_gaps_overlapping_finds_a_window_we_were_dark_for(conn):
    """The analysis-time question: 'can I trust this hour?'"""
    store.close_gap(conn, store.open_gap(conn, CH, 1000.0, "crash"), 2000.0)
    assert len(store.gaps_overlapping(conn, 1500.0, 1600.0)) == 1   # inside
    assert len(store.gaps_overlapping(conn, 900.0, 1100.0)) == 1    # straddles start
    assert len(store.gaps_overlapping(conn, 2500.0, 2600.0)) == 0   # clear of it


def test_an_open_gap_still_counts_as_overlapping(conn):
    """A gap with no end is still running. It must not vanish from the answer
    just because the process has not recovered yet."""
    store.open_gap(conn, CH, 1000.0, "watchdog_silence")
    assert len(store.gaps_overlapping(conn, 5000.0, 6000.0)) == 1


def test_heartbeat_records_last_socket_activity(conn):
    store.touch_heartbeat(conn, CH, 1000.0)
    store.touch_heartbeat(conn, CH, 1042.0)
    assert store.last_seen(conn, CH) == 1042.0


def test_heartbeat_counts_events(conn):
    for t in (1000.0, 1001.0, 1002.0):
        store.touch_heartbeat(conn, CH, t)
    row = conn.execute("SELECT events_total FROM heartbeat").fetchone()
    assert row["events_total"] == 3


def test_last_seen_is_none_before_any_capture(conn):
    """A first-ever run has no prior coverage to compare against, which is
    different from having a gap. Do not invent one."""
    assert store.last_seen(conn, CH) is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_gaps.py -v
```

Expected: FAIL with `AttributeError: module 'store' has no attribute 'open_gap'`

- [ ] **Step 3: Append the gap ledger to `seed/store.py`**

```python
# ------------------------------------------------------------- the gap ledger
#
# The product's central honesty mechanism. Reaction timing that was not captured
# is gone forever, so the tool must never let "we were not listening" look like
# "nobody reacted". Every window we cannot positively account for gets a row.


def open_gap(conn, channel, started_at, reason):
    """Start recording a window in which we were not capturing.

    reason: cold_start | watchdog_silence | crash | clean_shutdown
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
    """Total recorded time not capturing. Closed gaps only — an open gap has no
    measurable length yet, and guessing one would be the exact error this
    ledger exists to prevent."""
    row = conn.execute(
        "SELECT COALESCE(SUM(ended_at - started_at), 0.0) AS s "
        "FROM capture_gaps WHERE ended_at IS NOT NULL").fetchone()
    return float(row["s"])


def touch_heartbeat(conn, channel, at, inc=1):
    """Record socket activity. Called for every frame including pings, because
    a healthy idle connection still shows traffic — which is what lets the
    watchdog tell 'quiet room' apart from 'dead socket'."""
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_gaps.py -v
```

Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add seed/store.py tests/test_gaps.py
git commit -m "feat: add capture gap ledger so downtime cannot look like silence"
```

---

### Task 5: Import the existing JSONL capture

The hack-night capture in `seed/live_events.jsonl` is irreplaceable. Move it into the store before anything else depends on the DB.

**Files:**
- Modify: `seed/store.py` (append)
- Create: `tests/test_migrate.py`

**Interfaces:**
- Consumes: `store.record_event`, `store.record_message` from Task 3.
- Produces: `store.migrate_jsonl(conn, path) -> dict` with keys `events`, `messages`, `skipped`, `malformed`.

- [ ] **Step 1: Write the failing test**

`tests/test_migrate.py`:

```python
"""Importing the original JSONL capture. This data cannot be regenerated, so
the importer must be safe to run twice and must not stop at a torn line."""

import json

import pytest

import store


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init(c)
    yield c
    c.close()


def write_log(tmp_path, lines):
    p = tmp_path / "live_events.jsonl"
    p.write_text("\n".join(json.dumps(x) if isinstance(x, dict) else x
                           for x in lines))
    return str(p)


REACTION = {"kind": "reaction_added", "event_ts": "1786726570.000100",
            "ts_iso": "2026-08-14T16:56:10+00:00", "user": "Martin",
            "user_id": "U123", "emoji": "+1", "channel": "C1",
            "message_ts": "1786726564.102969"}
MESSAGE = {"kind": "message", "ts": "1786726564.102969", "channel": "C1",
           "ts_iso": "2026-08-14T16:56:04+00:00", "user": "Martin",
           "user_id": "U123", "text": "Tabs or spaces?"}


def test_imports_events_and_messages(tmp_path, conn):
    path = write_log(tmp_path, [MESSAGE, REACTION])
    result = store.migrate_jsonl(conn, path)
    assert result["events"] == 1
    assert result["messages"] == 1
    assert store.count_events(conn) == 1


def test_migration_is_rerunnable(tmp_path, conn):
    """Safe to re-run: the second pass stores nothing new and says so."""
    path = write_log(tmp_path, [MESSAGE, REACTION])
    store.migrate_jsonl(conn, path)
    second = store.migrate_jsonl(conn, path)
    assert second["events"] == 0
    assert second["skipped"] == 1
    assert store.count_events(conn) == 1


def test_a_torn_line_does_not_abort_the_import(tmp_path, conn):
    """A JSONL file killed mid-write ends in a partial line. The rest of the
    file is real captured data and must survive."""
    path = write_log(tmp_path, [REACTION, '{"kind": "message", "ts": "17867'])
    result = store.migrate_jsonl(conn, path)
    assert result["events"] == 1
    assert result["malformed"] == 1


def test_missing_file_reports_zero_rather_than_raising(tmp_path, conn):
    result = store.migrate_jsonl(conn, str(tmp_path / "nope.jsonl"))
    assert result == {"events": 0, "messages": 0, "skipped": 0, "malformed": 0}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_migrate.py -v
```

Expected: FAIL with `AttributeError: module 'store' has no attribute 'migrate_jsonl'`

- [ ] **Step 3: Append the importer to `seed/store.py`**

```python
# ---------------------------------------------------------------- JSONL import


def migrate_jsonl(conn, path):
    """Import an existing live_events.jsonl into the store.

    Idempotent, because record_event is. Tolerates a torn final line, which is
    what a killed listener leaves behind — the preceding lines are real captured
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_migrate.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Import the real hack-night capture**

```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'seed')
import store
c = store.connect(); store.init(c)
print(store.migrate_jsonl(c, 'seed/live_events.jsonl'))
print('events now:', store.count_events(c))
"
```

Expected: a dict reporting 21 events and 14+ messages imported, matching the counts measured from the JSONL. If `events` is 0 and `skipped` is high, the import already ran — that is the idempotency working, not a failure.

- [ ] **Step 6: Commit**

```bash
git add seed/store.py tests/test_migrate.py
git commit -m "feat: import existing JSONL capture into the store, re-runnably"
```

---

### Task 6: Extract the Slack event handlers so they can be reused

Pure refactor, no behaviour change. `seed/listen_slack.py` must keep working exactly as before.

**Files:**
- Modify: `seed/listen_slack.py` (extract two functions; `main()` stays and keeps using them)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `listen_slack.reaction_payload(event) -> dict | None` — builds the reaction record from a raw Slack event dict; returns None if the event is not a reaction.
  - `listen_slack.message_payload(event) -> dict | None` — same for messages.
  - Both return payloads with `user_id` populated and `user` left as `None`; name resolution is the caller's job. Task 7 consumes both.

- [ ] **Step 1: Add the two extraction functions**

Insert above `def tally():` in `seed/listen_slack.py`:

```python
def reaction_payload(e):
    """Raw Slack event -> the record we store, or None if it is not a reaction.

    `user` is deliberately left None. Resolving a user id to a display name is
    an HTTP call, and it must not happen on the capture path — see capture.py.
    """
    if e.get("type") not in ("reaction_added", "reaction_removed"):
        return None
    item = e.get("item", {}) or {}
    return {
        # event_ts IS the signal. Nothing else on this line is unique.
        "event_ts": e.get("event_ts"),
        "ts_iso": iso(e.get("event_ts")),
        "user": None,
        "user_id": e.get("user"),
        "emoji": e.get("reaction"),
        "item_user_id": e.get("item_user"),
        "channel": item.get("channel"),
        "message_ts": item.get("ts"),
    }


def message_payload(e):
    """Raw Slack event -> the message record, or None if it is not a plain message."""
    if e.get("type") != "message" or e.get("subtype"):
        return None
    return {
        "ts": e.get("ts"),
        "ts_iso": iso(e.get("ts")),
        "user": None,
        "user_id": e.get("user"),
        "text": e.get("text", ""),
        "channel": e.get("channel"),
        "thread_ts": e.get("thread_ts"),
    }
```

- [ ] **Step 2: Verify `listen_slack.py` still imports and its CLI still works**

```bash
.venv/bin/python seed/listen_slack.py --help
```

Expected: the argparse usage block, no traceback. (Running it for real needs Slack tokens; `--help` proves the module is intact.)

- [ ] **Step 3: Write a test for the extraction**

Create `tests/test_payloads.py`:

```python
"""The extracted handlers. These run on raw Slack event dicts, so they can be
tested without Slack — but note that no test here proves the live socket works."""

from listen_slack import message_payload, reaction_payload

RAW_REACTION = {
    "type": "reaction_added", "user": "U123", "reaction": "+1",
    "item_user": "U999", "event_ts": "1786726570.000100",
    "item": {"type": "message", "channel": "C1", "ts": "1786726564.102969"},
}


def test_reaction_payload_keeps_event_ts_verbatim():
    """event_ts is the entire product. It must survive untouched."""
    p = reaction_payload(RAW_REACTION)
    assert p["event_ts"] == "1786726570.000100"
    assert p["emoji"] == "+1"
    assert p["channel"] == "C1"
    assert p["message_ts"] == "1786726564.102969"


def test_reaction_payload_leaves_the_name_unresolved():
    """Name resolution is an HTTP call and must not sit on the capture path."""
    assert reaction_payload(RAW_REACTION)["user"] is None
    assert reaction_payload(RAW_REACTION)["user_id"] == "U123"


def test_reaction_payload_ignores_non_reactions():
    assert reaction_payload({"type": "message"}) is None


def test_message_payload_ignores_subtyped_messages():
    """Edits, joins and bot posts carry a subtype and are not room messages."""
    assert message_payload({"type": "message", "subtype": "channel_join"}) is None


def test_message_payload_extracts_text():
    p = message_payload({"type": "message", "user": "U123", "ts": "1786726564.102969",
                         "text": "Tabs or spaces?", "channel": "C1"})
    assert p["text"] == "Tabs or spaces?"
    assert p["user_id"] == "U123"
```

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/test_payloads.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add seed/listen_slack.py tests/test_payloads.py
git commit -m "refactor: extract Slack event payload builders for reuse"
```

---

### Task 7: `capture.py` — the watchdog

The core fix. Today `seed/listen_slack.py:180-186` runs `client.connect()` then `while True: time.sleep(1)`. If the client thread dies, the process stays alive with exit code 0, the tally freezes, and nothing is captured — a silently dead listener is indistinguishable from a quiet Slack.

**Files:**
- Create: `seed/capture.py`
- Create: `tests/test_watchdog.py`

**Interfaces:**
- Consumes: `store.*` (Tasks 3–4), `listen_slack.reaction_payload`, `listen_slack.message_payload` (Task 6).
- Produces:
  - `capture.Watchdog(silence_limit=90.0, clock=time.time)` with `.beat()`, `.is_silent()`, `.silent_for()`.
  - `capture.reconcile_startup(conn, channel, now) -> dict` — records the gap since the last known coverage.

- [ ] **Step 1: Write the failing test**

`tests/test_watchdog.py`:

```python
"""The watchdog. Socket Mode sends periodic pings, so a healthy but idle
connection still shows traffic. That is what makes SILENCE a usable failure
signal, distinct from 'nobody is reacting'.

The clock is injected, so these tests are instant and deterministic. The stub
lives here in tests/ and is never importable from the runtime path."""

import pytest

import store
from capture import Watchdog, reconcile_startup

CH = "C1"


class FakeClock:
    """An injected clock. Tests advance time explicitly instead of sleeping."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init(c)
    yield c
    c.close()


def test_a_fresh_watchdog_is_not_silent():
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    assert w.is_silent() is False


def test_silence_past_the_limit_is_detected():
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    clock.advance(91)
    assert w.is_silent() is True


def test_a_beat_clears_the_silence():
    """A ping counts. An idle room with a live socket must never trip this."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    clock.advance(89)
    w.beat()
    clock.advance(89)
    assert w.is_silent() is False


def test_silent_for_reports_the_dark_duration():
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    clock.advance(120)
    assert w.silent_for() == pytest.approx(120.0)


def test_first_ever_start_records_no_gap(conn):
    """No prior coverage is not the same as a gap in coverage. Inventing one
    would misreport the very thing this ledger exists to get right."""
    result = reconcile_startup(conn, CH, now=1000.0)
    assert result["gap_recorded"] is False
    assert store.open_gaps(conn) == []


def test_restart_records_the_window_since_last_coverage(conn):
    """The listener was up at t=1000 and is starting again at t=5000. Those
    4000 seconds were dark and must appear in the ledger."""
    store.touch_heartbeat(conn, CH, 1000.0)
    result = reconcile_startup(conn, CH, now=5000.0)
    assert result["gap_recorded"] is True
    assert result["dark_seconds"] == pytest.approx(4000.0)
    assert store.total_dark_seconds(conn) == pytest.approx(4000.0)


def test_an_unclosed_gap_from_a_previous_run_is_marked_as_a_crash(conn):
    """A gap with no end means the process died before it could close it."""
    store.touch_heartbeat(conn, CH, 1000.0)
    store.open_gap(conn, CH, 900.0, "watchdog_silence")
    reconcile_startup(conn, CH, now=5000.0)
    reasons = [g["reason"] for g in
               conn.execute("SELECT reason FROM capture_gaps").fetchall()]
    assert "crash" in reasons
    assert store.open_gaps(conn) == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_watchdog.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'capture'`

- [ ] **Step 3: Write `seed/capture.py`**

```python
"""Supervised capture daemon.

The listener it replaces had one fatal shape:

    client.connect()
    while True:
        time.sleep(1)

slack_sdk reconnects internally, but if that thread dies the loop sleeps
forever: process alive, exit code 0, tally frozen, nothing captured. A silently
dead listener looks exactly like a quiet Slack, and the timing it misses cannot
be recovered afterwards.

So this module treats SILENCE as failure. Socket Mode sends periodic pings, so a
healthy idle connection still shows traffic; no traffic means the socket is gone,
whatever the process thinks. Every dark window is written to the gap ledger.

    .venv/bin/python seed/capture.py --channel C0XXXXXXXXX
"""

import argparse
import os
import queue
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import store                                              # noqa: E402
from listen_slack import (load_env, message_payload,      # noqa: E402
                          reaction_payload)

SILENCE_LIMIT = float(os.getenv("CAPTURE_SILENCE_LIMIT", "90"))


class Watchdog:
    """Declares the connection dead after `silence_limit` seconds without traffic.

    The clock is injected so the behaviour can be tested without waiting.
    """

    def __init__(self, silence_limit=SILENCE_LIMIT, clock=time.time):
        self.silence_limit = silence_limit
        self.clock = clock
        self._last = clock()

    def beat(self):
        """Called on every socket frame, pings included."""
        self._last = self.clock()

    def silent_for(self):
        return self.clock() - self._last

    def is_silent(self):
        return self.silent_for() > self.silence_limit


def reconcile_startup(conn, channel, now):
    """Account for the window since this channel was last known to be covered.

    Three cases, and the difference between them matters:
      - never captured before  -> no gap. Absence of history is not a gap.
      - a previous run left an open gap -> it crashed; close it and say so.
      - we have a last_seen -> everything since then was dark. Record it.
    """
    result = {"gap_recorded": False, "dark_seconds": 0.0}

    for gap in store.open_gaps(conn):
        conn.execute("UPDATE capture_gaps SET reason='crash' WHERE id=?",
                     (gap["id"],))
        store.close_gap(conn, gap["id"], now)
        result["gap_recorded"] = True

    seen = store.last_seen(conn, channel)
    if seen is None:
        return result

    dark = now - seen
    if dark > 0:
        store.close_gap(conn, store.open_gap(conn, channel, seen, "cold_start"), now)
        result["gap_recorded"] = True
        result["dark_seconds"] = dark
    return result


def _resolver(web, conn, names, work, stop):
    """Resolve user ids to display names OFF the capture path.

    users_info is an HTTP round-trip. Doing it inside the event handler puts a
    network call in the middle of exactly the reaction bursts worth measuring.
    """
    while not stop.is_set():
        try:
            uid = work.get(timeout=0.5)
        except queue.Empty:
            continue
        if uid in names:
            continue
        try:
            u = web.users_info(user=uid)["user"]
            names[uid] = u.get("real_name") or u.get("name") or uid
        except Exception:
            names[uid] = uid          # visibly a raw id, never a fabricated name
        conn.execute("UPDATE events SET user=? WHERE user_id=? AND user IS NULL",
                     (names[uid], uid))
        conn.execute("UPDATE messages SET user=? WHERE user_id=? AND user IS NULL",
                     (names[uid], uid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="channel id to capture")
    ap.add_argument("--db", default=None, help="path, or a libsql:// URL")
    ap.add_argument("--silence-limit", type=float, default=SILENCE_LIMIT,
                    help="seconds of socket silence before declaring it dead")
    args = ap.parse_args()

    load_env()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not app_token.startswith("xapp-") or not bot_token.startswith("xoxb-"):
        print("SLACK_APP_TOKEN (xapp-) and SLACK_BOT_TOKEN (xoxb-) are both "
              "required. See README 'Capture a live Slack room'.")
        sys.exit(1)

    conn = store.connect(args.db)
    store.init(conn)

    now = time.time()
    rec = reconcile_startup(conn, args.channel, now)
    if rec["gap_recorded"]:
        print(f"  gap recorded: {rec['dark_seconds'] / 60:.1f} min not captured "
              f"before this start")
    else:
        print("  no prior coverage to account for")

    from slack_sdk import WebClient
    from slack_sdk.socket_mode.builtin import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    web = WebClient(token=bot_token)
    auth = web.auth_test()
    print(f"connected as {auth['user']} in {auth['team']}")

    dog = Watchdog(silence_limit=args.silence_limit)
    names, work, stop = {}, queue.Queue(), threading.Event()
    threading.Thread(target=_resolver, args=(web, conn, names, work, stop),
                     daemon=True).start()

    # Counters reflect the capture, not this process. A restart must not make
    # the tally read as though the room went quiet.
    counts = {"reaction_added": 0, "reaction_removed": 0, "message": 0}
    for row in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM events WHERE channel=? GROUP BY kind",
            (args.channel,)).fetchall():
        counts[row["kind"]] = row["n"]
    counts["message"] = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE channel=?",
        (args.channel,)).fetchone()["n"]

    def handle(client, req: SocketModeRequest):
        # Ack first, always. Slack retries anything unacked within 3s.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        dog.beat()
        store.touch_heartbeat(conn, args.channel, time.time())
        if req.type != "events_api":
            return
        e = req.payload.get("event", {})

        p = reaction_payload(e)
        if p and p.get("channel") == args.channel:
            if store.record_event(conn, e["type"], p):
                counts[e["type"]] += 1
                work.put(p["user_id"])
            return

        p = message_payload(e)
        if p and p.get("channel") == args.channel:
            if store.record_message(conn, p):
                counts["message"] += 1
                work.put(p["user_id"])

    def connect_client():
        c = SocketModeClient(app_token=app_token, web_client=web)
        c.socket_mode_request_listeners.append(handle)
        c.connect()
        dog.beat()
        return c

    client = connect_client()
    print(f"capturing {args.channel} -> {args.db or store.DEFAULT_PATH}")
    print(f"  watchdog: reconnects after {args.silence_limit:.0f}s of socket silence")
    print("  every dark window is recorded. Ctrl-C to stop.\n")

    gap_id = None

    def bye(*_):
        if gap_id is not None:
            store.close_gap(conn, gap_id, time.time())
        store.touch_heartbeat(conn, args.channel, time.time(), inc=0)
        stop.set()
        print(f"\nstopped. {counts['reaction_added']} reactions captured. "
              f"{store.total_dark_seconds(conn) / 60:.1f} min dark on record.")
        sys.exit(0)

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    backoff = 1.0
    while True:
        time.sleep(1)
        if dog.is_silent():
            if gap_id is None:
                gap_id = store.open_gap(conn, args.channel,
                                        time.time() - dog.silent_for(),
                                        "watchdog_silence")
                print(f"\n  !! {dog.silent_for():.0f}s of socket silence -- "
                      f"reconnecting. Gap {gap_id} open.")
            try:
                client.close()
            except Exception:
                pass
            try:
                client = connect_client()
                store.close_gap(conn, gap_id, time.time())
                print(f"  reconnected. Gap {gap_id} closed.")
                gap_id, backoff = None, 1.0
            except Exception as exc:
                print(f"  reconnect failed ({type(exc).__name__}); "
                      f"retrying in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        else:
            print(f"\r  up  reactions {counts['reaction_added']:>4}  "
                  f"messages {counts['message']:>4}  "
                  f"quiet {dog.silent_for():>4.0f}s", end="", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_watchdog.py -v
```

Expected: 7 PASS.

- [ ] **Step 5: Verify the CLI is intact**

```bash
.venv/bin/python seed/capture.py --help
```

Expected: argparse usage showing `--channel`, `--db`, `--silence-limit`. No traceback.

- [ ] **Step 6: Run the whole suite**

```bash
.venv/bin/python -m pytest -v
```

Expected: 41 PASS (11 shapes + 6 store + 8 gaps + 4 migrate + 5 payloads + 7 watchdog).

- [ ] **Step 7: Commit**

```bash
git add seed/capture.py tests/test_watchdog.py
git commit -m "feat: supervised capture daemon that records its own downtime"
```

---

### Task 8: Dashboard reads the store and reports staleness honestly

Fixes audit finding #2: `seed/live_server.py:255` swallows fetch failures with `.catch(function(){})`, leaving stale numbers under a pulsing LIVE indicator.

**Files:**
- Modify: `seed/live_server.py` — `read_events()`, `build()`, and the page's JS

**Interfaces:**
- Consumes: `store.connect`, `store.init`, `store.open_gaps`, `store.total_dark_seconds`, `store.last_seen`.
- Produces: `/events.json` gains `served_at`, `open_gaps`, `dark_minutes`.

- [ ] **Step 1: Replace `read_events()` with a store read**

Replace the whole `read_events()` function in `seed/live_server.py` with:

```python
def read_events():
    """Read from the capture store.

    An empty store yields an empty page ("Waiting for the first reaction…"),
    which is correct: it means nothing has been captured, not that something
    went wrong. Run `store.migrate_jsonl` once to bring a legacy JSONL in.
    """
    conn = store.connect(os.getenv("CAPTURE_DB"))
    store.init(conn)
    rows = conn.execute(
        "SELECT kind, channel, message_ts, user_id, user, emoji, "
        "       event_ts, ts_iso, raw FROM events ORDER BY event_ts").fetchall()
    out = []
    for r in rows:
        e = json.loads(r["raw"])
        e["kind"] = r["kind"]
        e["user"] = r["user"] or r["user_id"]   # raw id if unresolved: never invented
        out.append(e)
    for m in conn.execute("SELECT * FROM messages").fetchall():
        out.append({"kind": "message", "ts": m["ts"], "channel": m["channel"],
                    "user": m["user"] or m["user_id"], "text": m["text"],
                    "ts_iso": m["ts_iso"]})
    conn.close()
    return out
```

Add at the top of the file, after the existing imports:

```python
sys.path.insert(0, HERE)
import store  # noqa: E402
```

- [ ] **Step 2: Add coverage honesty to the payload**

In `build()`, replace the `return {` block's opening so the returned dict also carries coverage state. Add these lines immediately before `return {`:

```python
    conn = store.connect(os.getenv("CAPTURE_DB"))
    store.init(conn)
    gaps = [{"since": g["started_at"], "reason": g["reason"]}
            for g in store.open_gaps(conn)]
    dark_minutes = round(store.total_dark_seconds(conn) / 60, 1)
    conn.close()
```

and add these three keys inside the returned dict:

```python
        "served_at": time.time(),
        "open_gaps": gaps,
        "dark_minutes": dark_minutes,
```

Add `import time` to the imports at the top of the file.

- [ ] **Step 3: Replace the swallowed catch with a staleness banner**

In `PAGE`, replace the final line of `tick()`:

```javascript
 }).catch(function(){});
```

with:

```javascript
  stale(false, d.served_at);
  gapline(d);
 }).catch(function(){ stale(true, null) });
```

Add these two functions immediately above `function tick(){`:

```javascript
var lastGood = null;
function stale(isStale, servedAt){
 if (!isStale) { lastGood = servedAt;
   document.body.classList.remove('stale');
   banner.textContent = ''; return; }
 document.body.classList.add('stale');
 var age = lastGood ? Math.round(Date.now()/1000 - lastGood) : null;
 banner.textContent = age === null
   ? 'NOT CONNECTED — this page has never reached the capture server.'
   : 'STALE — no update for ' + age + 's. These numbers are frozen, not live.';
}
function gapline(d){
 if (d.open_gaps && d.open_gaps.length) {
   gaps.textContent = 'CAPTURE GAP OPEN (' + d.open_gaps[0].reason
     + ') — reactions are being missed right now.';
 } else if (d.dark_minutes > 0) {
   gaps.textContent = d.dark_minutes + ' min not captured on record.';
 } else { gaps.textContent = ''; }
}
```

- [ ] **Step 4: Add the banner elements and their styling**

In `PAGE`, immediately after `<div class="top">...</div>`, add:

```html
<div id="banner" class="banner"></div>
<div id="gaps" class="gaps"></div>
```

Add to the `<style>` block:

```css
.banner:empty,.gaps:empty{display:none}
.banner{background:var(--red);color:#fff;font:800 13px var(--mono);
letter-spacing:.08em;padding:11px 15px;border-radius:10px;margin-bottom:14px}
.gaps{background:#2a2214;color:var(--yellow);font:700 12px var(--mono);
padding:9px 14px;border-radius:10px;margin-bottom:14px}
/* a stale page must not keep pulsing a green LIVE dot */
body.stale .dot{background:var(--red);box-shadow:none;animation:none}
body.stale .live{color:var(--red)}
```

- [ ] **Step 5: Verify the server starts and serves the new keys**

```bash
.venv/bin/python seed/live_server.py &
sleep 2
curl -s http://localhost:8765/events.json | .venv/bin/python -m json.tool | head -20
kill %1
```

Expected: JSON containing `served_at`, `open_gaps`, and `dark_minutes`, plus the existing `reactions` / `people` / `cards` keys populated from the imported hack-night capture.

- [ ] **Step 6: Verify the staleness banner appears when the server dies**

```bash
.venv/bin/python seed/live_server.py &
sleep 2
open http://localhost:8765
```

Watch the page, then `kill %1`. Within ~2 seconds the green dot must turn red and the banner must read `STALE — no update for Ns.` Confirm it does, then close the tab.

- [ ] **Step 7: Commit**

```bash
git add seed/live_server.py
git commit -m "fix: dashboard reads the store and reports staleness instead of freezing"
```

---

## Verification

Run the full suite:

```bash
.venv/bin/python -m pytest -v
```

Expected: **41 passing.**

Confirm the zero-install runtime path still works, which is the constraint most easily broken by this plan:

```bash
python3.12 seed/shapes.py --dialects --twins | head -20
```

Expected: the shapes report, no ImportError. If this fails, a runtime module has picked up a dev dependency and must be fixed before the work is called done.

**State alongside any result:** no test in this suite contacts Slack, Cognee, or Qdrant. A green run proves the store, the ledger, the classifier and the watchdog logic behave; it does not prove the live socket path works. That still requires a real capture run.

## Out of scope for this plan

Covered by the spec, deferred to a second plan: deployment units (`launchd`/`systemd`), audit findings #1, #3 and #4, the logo, and the LinkedIn post.
