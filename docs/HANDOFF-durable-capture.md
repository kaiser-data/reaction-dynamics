# Handoff — durable capture

**Branch:** `feat/durable-capture` (off `main`)
**Date:** 2026-08-15
**Status:** implementation complete and live-verified. **47 tests passing.
Nothing committed.**

Read this before the plan. The plan
(`docs/superpowers/plans/2026-08-15-durable-capture.md`) records what was
*intended*; several of its assertions turned out to be wrong on contact with the
codebase, and they are corrected here. Where the two disagree, this document is
the one that was checked against a running system.

---

## 1. What this branch does

Reaction Dynamics could not tell "nobody reacted" apart from "we were not
listening." Per-reaction timing exists only in the live Slack event and cannot
be re-fetched, so that ambiguity silently corrupts every later analysis: a
listener down from 09:00 to 17:00 left no trace, and the hole read as a quiet
morning.

The tool now records its own blind spots. That is the headline capability.

The original bug, at the old `seed/listen_slack.py:180`:

```python
client.connect()
while True:
    time.sleep(1)
```

`slack_sdk` reconnects internally, but if that thread died this loop slept
forever — process alive, exit code 0, tally frozen, capturing nothing.

## 2. Working tree

Nothing is committed. `git status` at time of writing:

| | |
|---|---|
| **New** | `seed/store.py`, `seed/capture.py`, `tests/` (7 files), `pyproject.toml`, `docs/superpowers/`, this file |
| **Modified** | `.gitignore`, `README.md`, `dashboard.html`, `site/index.html`, `seed/ask.py`, `seed/ingest_cognee.py`, `seed/listen_slack.py`, `seed/live_server.py` |

`presentation.html` is also modified (+21/−17) but **predates this work** — it
is unrelated and should probably not ride along in a commit for this branch.

| File | Responsibility |
|---|---|
| `seed/store.py` | SQLite persistence, idempotent writes, gap ledger, JSONL import |
| `seed/capture.py` | Supervised daemon: watchdog, startup reconciliation, off-path name resolution |
| `tests/` | 47 tests across 6 test files |
| `pyproject.toml` | Dev-only pytest; runtime stays stdlib |

- `seed/listen_slack.py` — extracted `reaction_payload()` / `message_payload()`.
  **Its `main()` is untouched and still works**; the old JSONL path is intact.
- `seed/live_server.py` — reads the store instead of re-scanning JSONL; the
  swallowed `.catch(function(){})` is replaced with a staleness banner that
  turns the LIVE dot red, plus an open-gap warning line.
- `.gitignore` — `capture.db*` and `.pytest_cache/`. **Captured Slack content
  must never be committed.**

## 3. Verify

```bash
.venv/bin/python -m pytest              # 47 passed, ~0.05s
python3.12 seed/shapes.py --dialects    # must run with zero installs
```

The second command is the constraint most easily broken by future work: if a
runtime module ever picks up a dev dependency, this fails.

**State alongside any green run:** no test contacts Slack, Cognee, or Qdrant.
The suite proves the store, ledger, classifier and watchdog logic behave. It
does not prove the live socket path works. This is not a formality — see §5.

## 4. Key design decisions

- **Storage is SQLite, not libSQL/Turso.** Measured from the real capture:
  8 reactions/hour, busiest 60s had 3. Even 100× that is 0.22 writes/sec —
  about five orders of magnitude under SQLite's ceiling. Scale is not the
  constraint. `store.connect()` has a one-function seam so a `libsql://` URL
  switches to a Turso embedded replica later without touching schema, queries,
  or tests. The real argument for Turso here is off-machine durability of
  irreplaceable data, not throughput.
- **Silence is the failure signal — but "silence" had to be redefined.** The
  original premise was that Socket Mode pings arrive as events, so any healthy
  connection shows traffic. **That premise is false** (§5). Liveness is now the
  most recent of two independent signals: a Slack event (`beat()`), or the
  SDK's ping/pong stamp, polled (`socket_liveness()`). No traffic on *either*
  for `--silence-limit` seconds (default 90) means the socket is gone.
- **Absence of history ≠ a gap.** A first-ever run records no gap. Inventing
  one would be the exact error the ledger exists to prevent.
- **Open gaps are excluded from `total_dark_seconds`** — an unfinished gap has
  no measurable length yet, and guessing would fabricate the number.
- **Names resolve off the capture path.** `users_info` is an HTTP round-trip;
  it used to sit inside the event handler, i.e. in the middle of exactly the
  bursts worth measuring. Now a background thread backfills; unresolved users
  display as raw Slack ids, never as invented names.

## 5. Live capture run and the defect it found

Run against `#emojie-lab` (`C0BQ7FGF82H`), 2026-08-15. **The live socket path
found a defect all 41 tests passed over.**

Verified working against the real socket: `load_env()` → `auth_test()` →
connected as `cogneegraph` in `HackNight`; `reconcile_startup` on an empty
heartbeat correctly wrote no gap; counters restored from the DB
(`reactions 21  messages 39`) so a restart does not read as a quiet room;
`SIGTERM` closed the open gap and stamped the heartbeat.

### The defect

On a healthy connection with no reactions, the quiet counter climbed with no
reset, and at exactly 90s:

```
  !! 90s of socket silence -- reconnecting. Gap 1 open.
  reconnected. Gap 1 closed.
```

A **fabricated 92-second gap** for a socket that was never down.

`dog.beat()` lived only inside `handle`, which `slack_sdk` calls only for
`SocketModeRequest` frames. So only a Slack **event** could feed the watchdog,
and a quiet channel produces none for hours while its socket is fine.

Two failures followed, in opposite directions:

1. **False positives while running.** Any channel quieter than 90s manufactures
   a phantom gap and forces a needless reconnect, forever. Inventing gaps
   discredits the ledger as surely as missing them.
2. **False negatives across restarts.** `touch_heartbeat` sat on the same dead
   path, so an idle run stamped no heartbeat. If such a run crashed, the next
   boot saw `last_seen is None` and recorded *no* `cold_start` gap — a genuinely
   dark window going unrecorded.

### The obvious fix is wrong — recorded so it is not re-derived

The first diagnosis was *"pings go to the sibling `message_listeners` list,
which was never registered — register it."* **That would not have worked.**
Socket Mode keepalives are WebSocket **PING/PONG control frames**, handled
inside `slack_sdk/socket_mode/builtin/connection.py` at the protocol layer. They
are not JSON data frames and reach *no* listener list at all. `message_listeners`
sees only text frames such as `hello` and `disconnect`, which on an idle channel
are as rare as events. Registering it would have left the bug in place while
looking like a fix — and would have passed a plausible-looking test.

What the SDK does expose is `client.current_session.last_ping_pong_time`,
stamped on every keepalive. The watchdog must **poll** it rather than be called
by it.

### The fix, applied and verified

```python
def socket_liveness(client):
    session = getattr(client, "current_session", None)
    stamp = getattr(session, "last_ping_pong_time", None)
    return float(stamp) if stamp else None        # None, never a guess
```

Wired as `Watchdog(..., probe=lambda: socket_liveness(state["client"]))`.
`state["client"]` is a dict entry, not a closed-over name, because `client` is
rebound on every reconnect.

`socket_liveness` returns `None` when the reading is unavailable — before
connect, or on a future SDK that drops the attribute. A liveness probe that
failed *open* would silently disarm the watchdog, which is worse than having
none; failing to `None` falls back to beat-only behaviour, which is merely the
old bug rather than a new invisible one.

| | before fix | after fix |
|---|---|---|
| quiet counter, idle channel | climbs 1s → 90s, never resets | cycles 0–4s |
| phantom trips in 130s | 1 (at 90s) | **0** |
| `capture_gaps` after run | 1 fabricated row, 92.0s | **empty** |
| `total_dark_seconds` | 92.0 | **0.0** |

Clean shutdown re-verified: `stopped. 21 reactions captured. 0.0 min dark on
record.` Six regression tests added under "ping-level liveness" in
`tests/test_watchdog.py`, including
`test_a_quiet_but_pinging_socket_does_not_trip_the_watchdog`, which drives ten
simulated minutes of pings with zero events.

### Why the tests missed it

`tests/test_watchdog.py` drove `Watchdog` through an injected clock and called
`.beat()` directly. It proved the timer arithmetic and nothing about what feeds
it. **The gap between "the logic is correct" and "the logic is connected" is
invisible to a test that supplies its own inputs.** Worth remembering before
trusting any green suite on this branch.

## 6. Also done this session

- **Audit finding #1** — routing claims corrected in `seed/ask.py` (docstring)
  and `README.md`: `keyword_route()` is unconditional; the LLM only writes the
  closing sentence. The audit listed two sites; there were **four** — the same
  claim also appears as an SVG label at `dashboard.html:555` and
  `site/index.html:555`, found only by grepping after fixing the listed two.
  Assume any documented claim appears in more places than an audit enumerates.
- **Audit finding #3** — `✅ running` → `✅ captured 2026-08-14` in
  `dashboard.html` and `site/index.html`. No present-tense claim about a
  listener that is not listening.
- **Audit finding #4** — `seed/ingest_cognee.py` collects per-collection search
  failures, prints them, and distinguishes *searched cleanly, matched nothing*
  (check the ingest) from *searches failed* (fix those first). Same principle as
  the gap ledger: a failure and an empty result are different facts.
- **README** — `capture.py` is now the documented capture path;
  `listen_slack.py` demoted to "still works, but cannot tell you whether it was
  running." New section on the gap ledger, the four gap reasons, and the two
  honesty rules. New Tests section stating what a green run does *not* prove.
- **Superpowers issue filed** —
  [obra/superpowers#2153](https://github.com/obra/superpowers/issues/2153),
  proposing a chain-of-handoffs execution mode. Local source of truth:
  `docs/superpowers/issue-chain-of-handoffs.md`.

## 7. Not yet done

1. ~~**Correct the spec.**~~ **Done.** §6 of
   `docs/superpowers/specs/2026-08-15-production-hardening-design.md` now opens
   with a correction notice and describes the two-source rule and the polled
   `last_ping_pong_time`. As with audit finding #1, the claim lived in **more
   places than the item named**: grepping turned up four live sites, not one —
   the spec, plus the module docstrings of `seed/capture.py`, `seed/store.py`
   (`touch_heartbeat`), and `tests/test_watchdog.py`. All four are corrected.
   The plan still contains the false premise in four places **by design** — it
   is the record of intent — but now carries a `SUPERSEDED` banner naming this
   specific falsehood, so a reader who opens the plan first is warned before
   reaching it. Verified by re-grepping to empty outside the plan.
2. **`client.is_connected()` is unused.** An explicit disconnect is knowable
   immediately and need not wait out the full 90s silence limit. Cheap, not
   urgent.
3. `launchd` / `systemd` units (crash-restart *under* the watchdog).
4. Logo (SVG mark + image-generator prompt).
5. LinkedIn post — written last, from what is true by then. Note the lead has
   changed: it is no longer "the tool admits when it wasn't looking" but "the
   tool's own ledger caught the tool inventing a gap, and only a live run found
   it."

## 8. Environment notes

- `.venv` is a **symlink** to `../cognee-qdrant-slack-test/.venv`, is
  uv-managed, and has **no pip**. Install with
  `uv pip install <pkg> --python .venv/bin/python`. pytest 9.1.1 was added
  there, which also affects that sibling project.
- `pyproject.toml` needs `pythonpath = ["seed", "tests"]` — `tests/__init__.py`
  makes it a package, so `conftest` is not importable as a top-level module
  without it.
- A `live_server.py` from the hack night is **still running on port 8765**
  (PID 14390, still alive at time of writing) serving the old code. Use
  `PORT=8799` for testing, or stop it.
- Channel `C0BQ7FGF82H` is `#emojie-lab`; the bot (`cogneegraph`) is a member.
  `SLACK_TEST_CHANNEL` in `.env` points at a different, non-existent channel
  (`channel_not_found`) — do not use it.
- `seed/capture.db` currently holds **23 events, 39 messages, 0 gaps, 0.0s dark
  time**, all of it the hack-night capture. The one fabricated gap row and its
  stale heartbeat were deleted after the fix; captured events were untouched.

## 9. Corrections to the plan

Places where the plan is wrong and the code or the world is right:

- Plan said `pip install pytest`; the venv has no pip, so `uv pip` was used.
- Plan predicted the JSONL import would yield 21 events / 14+ messages; actual
  was **23 events / 39 messages**. The extra events are `reaction_removed`;
  totals reconcile against the 62-line JSONL.
- `test_migration_is_rerunnable` asserted `skipped == 1`; the correct value is
  **2** (the message and the reaction are both already known on re-run). **The
  test assertion was wrong, not the code** — the exact case where a fresh reader
  "fixes" working code.
- The plan's `capture.py` used a bare `gap_id = None` closure; the implemented
  version uses a `state` dict, which the watchdog probe now also depends on.
