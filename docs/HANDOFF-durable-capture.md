# Handoff — durable capture

**Branch:** `feat/durable-capture` (off `main`)
**Date:** 2026-08-15
**Status:** implementation complete and live-verified. **63 tests passing.
Committed** on `feat/durable-capture`, starting at `e8e1234` — **not pushed**.

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

All of the below is committed on `feat/durable-capture`, **not pushed** —
`e8e1234` is the bulk of it, with later commits for the disconnect path, the
restart-loop fix (§10), and `deploy/`. `presentation.html` was deliberately left
out and is the only dirty file. What the branch contains:

| | |
|---|---|
| **New** | `seed/store.py`, `seed/capture.py`, `tests/` (7 files), `pyproject.toml`, `docs/superpowers/`, this file |
| **Modified** | `.gitignore`, `README.md`, `dashboard.html`, `site/index.html`, `seed/ask.py`, `seed/ingest_cognee.py`, `seed/listen_slack.py`, `seed/live_server.py` |

`presentation.html` is also modified (+21/−17) but **predates this work**, so it
was excluded from the commit and remains dirty. Decide separately whether it
belongs on this branch at all.

| File | Responsibility |
|---|---|
| `seed/store.py` | SQLite persistence, idempotent writes, gap ledger, JSONL import |
| `seed/capture.py` | Supervised daemon: watchdog, startup reconciliation, off-path name resolution |
| `tests/` | 63 tests across 6 test files |
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
.venv/bin/python -m pytest              # 63 passed, ~0.1s
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
- **An explicit disconnect is a second, independent trip condition.** Silence is
  a *timeout* and cannot speak before the limit elapses; `is_connected() ==
  False` is knowable now, so waiting 90s to agree with it is 90s of avoidable
  dark time (reason `disconnected`). Deliberately hard to fire: only an explicit
  `False` counts, and only outside a 5s post-connect grace window, because the
  SDK also reports `False` mid-handshake. Unknown is never down — the opposite
  failure to the ping bug, and just as capable of corrupting the ledger.
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

## 7. Follow-up work — all five items closed

Every item that was open when this handoff was written is now done. They are kept
here with their outcomes rather than deleted, because three of them changed the
code and two of them found bugs. **What is genuinely still open is listed in §11.**

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
2. ~~**`client.is_connected()` is unused.**~~ **Done.** `socket_connected()`
   plus `Watchdog.is_down()` make an explicit disconnect a second, independent
   trip condition, so it no longer costs a full 90s of dark time. New gap reason
   `disconnected`; enumerated in five places, all updated (`store.open_gap`
   docstring, README table, spec schema comment, spec §6, and the code).

   Two things worth knowing before touching it:

   - **It is guarded by `CONNECT_GRACE` (default 5s, `CAPTURE_CONNECT_GRACE`).**
     The SDK reports `is_connected() == False` *during the handshake too*.
     Without the grace window the daemon would open a gap, reconnect, open a
     gap — once a second, on a socket coming up perfectly normally. That is the
     mirror image of the ping bug: the first bug under-trusted a signal, this
     one would over-trust it.
   - **Unknown is never treated as down**, matching `socket_liveness`. Checked
     against the real SDK rather than assumed, which mattered:
     `SocketModeClient.is_connected()` **raises `AttributeError`** before
     connect (no `current_session`), so the `except` in `socket_connected` is
     load-bearing, not decoration. Two contract tests pin this and
     `pytest.importorskip` out where `slack_sdk` is absent, so the suite keeps
     running without the `live` extra.
3. ~~`launchd` / `systemd` units (crash-restart *under* the watchdog).~~
   **Done.** `deploy/` holds both as templates (`__PYTHON__`, `__REPO__`,
   `__CHANNEL__`) plus `deploy/README.md`. The plist is `plutil -lint` clean
   before and after substitution; the systemd unit was only INI-parsed, since
   there is no systemd on this machine — **its runtime semantics are unverified**.

   Two non-default settings, both deliberate: `StartLimitIntervalSec=0`, because
   systemd otherwise gives up after 5 starts in 10s and leaves the unit dead —
   the daemon would stop capturing *and* stop recording that it stopped; and
   `RestartSec`/`ThrottleInterval` at 15s, which sets ledger row volume during a
   sustained outage rather than the accuracy of the total.

   **Writing these surfaced a real bug — see §10.** Do not install supervision on
   any build whose `reconcile_startup` lacks the watermark stamp described there.
4. ~~Logo (SVG mark + image-generator prompt).~~ **Done.** Five SVGs in
   `docs/assets/` (`logo-mark`, `logo-favicon`, `logo-lockup`,
   `logo-lockup-light`, `logo-mono`), plus `LOGO.md` with usage rules and the
   generator prompt, plus `logo-preview.html` as a proof sheet. Favicon wired
   into `dashboard.html` and all three `site/` pages — `site/` is a
   self-contained deploy root, so it got its own copy rather than a `../docs/`
   path that would 404 once deployed.

   **The first design was wrong and rendering it is how I found out.** Dots on a
   baseline read as a lowercase "L" followed by a smear, the three "burst" dots
   merged into a dash by 48px, and the rejected even-spacing sample in my own
   comparison panel read *better* than the chosen one. Redrawn as ticks of
   uneven height and spacing. Two lessons worth keeping: a `--` inside an XML
   comment is illegal and silently killed the whole lockup file (`xmllint
   --noout` catches it); and the proof sheet now loads the real `.svg` files via
   `<img>` rather than inlined copies, so it cannot drift from the assets — the
   same trap as the routing claims in §6.
5. ~~LinkedIn post~~ **Done.** `docs/LINKEDIN-POST.md` now holds three
   ready-to-post drafts. A and B are the hack-night post and already carried the
   first-place result; **Draft C is new** and is the durable-capture story.

   It is a separate post, not a section bolted onto A — the file's own rule is
   one idea per post, and this one has a different audience (engineers) and a
   different lead. The lead did move as predicted, and then moved again: it is
   now "I built a tool whose only job is to record when it wasn't listening. It
   lied about that. Twice, in opposite directions." Both bugs (§5 and §10) are
   in it, along with the line that ties them together — 63 green tests, none of
   which touched a real socket.

   Counts are measured, not estimated: 2,449 units unstyled, 551 spare against
   LinkedIn's 3,000. My first guess of ~2,780 was wrong by 331, which is why the
   header now carries a measured figure. Note the file's own caveat that styled
   Unicode glyphs cost 2× — the spare figure assumes no styling.

   First place is also now stated in `README.md` and `SUBMISSION.md`, which had
   the event but not the result.

## 8. Environment notes

- `.venv` is a **symlink** to `../cognee-qdrant-slack-test/.venv`, is
  uv-managed, and has **no pip**. Install with
  `uv pip install <pkg> --python .venv/bin/python`. pytest 9.1.1 was added
  there, which also affects that sibling project.
- `pyproject.toml` needs `pythonpath = ["seed", "tests"]` — `tests/__init__.py`
  makes it a package, so `conftest` is not importable as a top-level module
  without it.
- The hack-night `live_server.py` that held **port 8765** (PID 14390) was
  stopped; the port is free and the default now works. If something is on 8765
  again, it is not that process.
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

## 10. The restart-loop overcount (found writing the deploy units)

The second defect this branch found by trying to *use* the thing rather than
test it. Latent until something restarts the daemon automatically — which is
precisely what §7.3 adds, so writing supervision is what set it off.

`reconcile_startup` recorded the dark window as `(last_seen → now)`, and
`last_seen` only advanced on a captured event or a clean shutdown. A run that
died before either — revoked token, Slack unreachable, DNS blip, OOM — left the
mark untouched. Every restart therefore re-measured **from the same original
point**, and the ledger summed overlapping windows:

```
restart at t=1030   real dark  30s   ledger says   30.0s
restart at t=1060   real dark  60s   ledger says   90.0s
restart at t=1090   real dark  90s   ledger says  180.0s
```

Ninety seconds of downtime billed as one hundred and eighty, growing without
bound for as long as the supervisor kept trying. This is the phantom-gap failure
class pointing the other way: **over-reporting dark time discredits the ledger
exactly as fast as under-reporting it**, and this one scales with how hard the
supervisor works to recover.

A second, smaller overlap sat underneath it: a `crash` gap already spans from
where coverage stopped through to the current start, and the code then added a
`cold_start` gap over the same window.

### The fix

`reconcile_startup` now stamps the heartbeat to `now` on the way out, so each
start accounts only for the window since the previous one — windows tile instead
of overlapping — and a closed crash gap suppresses the redundant `cold_start`.

The watermark is deliberately *not* a claim of coverage. It means "everything
before this point is in the ledger", which is true the moment the gap is written.
Conflating those two meanings in one column is what caused the bug; the docstring
now says which one it is.

**Known residual, stated rather than discovered later:** the window between
process start and a successful connect — normally well under a second — is not
recorded on a successful start. That is a bounded undercount accepted to remove
an unbounded overcount. When the connect *fails*, the next start measures from
that point, so the window is recorded after all.

Six regression tests under "restart loops and the ledger" in
`tests/test_watchdog.py`, including
`test_a_supervised_crash_loop_reports_real_downtime` (ten crash-restarts 30s
apart must report five minutes, not fifty) and
`test_a_long_outage_is_still_reported_in_full`, which guards against "fixing"
the overlap by quietly under-reporting instead.

### Why the tests missed it, again

Same shape as §5, one level up. The existing tests called `reconcile_startup`
**once**. Nothing exercised the sequence a supervisor produces, because until
this session nothing in the repo restarted the daemon. The gap between "correct
in isolation" and "correct in the loop it actually runs in" is invisible to a
test that only runs it once — just as §5's gap was invisible to a test that
supplied its own inputs.

Worth generalising before the next component: **this branch's two real defects
were both found by running the system, not by testing the units.** Both were in
code with green tests around it.

## 11. What is actually still open

Short list, and none of it is a code change waiting to be written.

**Nothing is pushed.** Five commits sit on `feat/durable-capture` and the remote
has never seen them. `origin` is a public repo, so pushing is a publishing
decision, not a mechanical one — deliberately left to a human. Nothing in the
branch carries tokens or captured Slack content; the channel id that does appear
was already public in `docs/HANDOFF.md`.

**`presentation.html` is still dirty** (+21/−17) and still predates this work. It
was excluded from every commit here. Someone should decide whether it belongs on
this branch or gets reverted.

**A live run was done on 2026-08-18 and the key regression is now verified.**
Supervised daemon against `#emojie-lab`, real socket, ~130 s idle:

```
  gap recorded: 4752.1 min not captured before this start
  connected as cogneegraph in HackNight
  watchdog: reconnects at once on an explicit disconnect, or after 90s of socket silence
  ...
  stopped. 21 reactions captured. 4752.1 min dark on record.
```

- **Max `quiet` value reached: 5 s.** It resets on every ping/pong stamp, so it
  never approaches 90. Before the §5 fix it climbed monotonically and tripped.
  **Zero phantom gaps** across the idle period — the §5 defect is confirmed fixed
  against a real socket, not just against stubs.
- **Exactly one gap row**, `cold_start`, 4752.1 min. That is genuine: the previous
  run ended 2026-08-15 and nothing was listening in between. Correct accounting,
  and — the §10 fix — *one* row rather than one per restart.
- **Clean `SIGTERM` shutdown** closed it: 1 gap row, 0 open, 4752.1 min dark on
  record, matching to the tenth of a minute.

Still unexercised live: the `disconnected` fast path and `CONNECT_GRACE` (would
need a real mid-session socket drop) and the `crash`-reason path (would need a
`SIGKILL` mid-run). Both are covered by tests and by static checks against the
installed SDK. Worth doing opportunistically; no longer the top risk.

**The systemd unit has never run.** There is no systemd on this machine, so it was
INI-parsed only. The launchd plist is `plutil -lint` clean but also uninstalled.

**Two things a future reader should not re-derive.** The `message_listeners` fix
does not work (§5). The ping premise is false everywhere it still appears in the
plan (§9), which is why the plan carries a `SUPERSEDED` banner.

---

## Closing note

Two real defects on this branch. Both in code with green tests around it. Both
found by running the system rather than by testing the units — one by watching a
live socket, one by writing the supervision that would have triggered it.

They failed in opposite directions: the first invented downtime that never
happened, the second double-counted downtime that did. That symmetry is the useful
part. A ledger's credibility is not "did it catch the outage" — it is "is the
number right", and both directions of wrong destroy it equally.

The test suite grew 47 → 63 across this work. Worth being clear that the new tests
did not find these bugs; they were written afterwards, to hold the fixes. What
found the bugs was use.
