"""The watchdog. SILENCE is the failure signal, distinct from 'nobody is
reacting' -- but only when measured from both liveness sources: a Slack event
(beat) and the SDK's polled ping/pong stamp (probe). Keepalives are WebSocket
control frames and reach no listener, so an events-only watchdog trips on any
channel quieter than the limit and invents a gap. The "ping-level liveness"
cases below are the regression tests for exactly that.

The clock is injected, so these tests are instant and deterministic. The stub
lives here in tests/ and is never importable from the runtime path."""

import pytest

import store
from capture import Watchdog, reconcile_startup, socket_liveness

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


# --------------------------------------------------------- ping-level liveness
#
# Regression tests for the defect the live run found on 2026-08-15: on an idle
# channel the watchdog tripped every 90s and wrote a phantom gap for a socket
# that was never down.
#
# The cause was that `beat()` was reachable only from the events listener.
# Socket Mode keepalives are WebSocket PING/PONG *control frames* -- the SDK
# handles them inside the connection and they never reach any listener list, so
# a quiet room looked identical to a dead socket. The SDK does stamp them, and
# that stamp is the ping-level liveness signal the watchdog was missing.
#
# The original tests could not have caught this: they call beat() directly and
# so prove the timer arithmetic while proving nothing about what calls it.


class FakeSession:
    def __init__(self, last_ping_pong_time=None):
        self.last_ping_pong_time = last_ping_pong_time


class FakeClient:
    def __init__(self, session=None):
        self.current_session = session


def test_a_quiet_but_pinging_socket_does_not_trip_the_watchdog():
    """THE regression test. No events for 10 minutes, but the SDK keeps
    stamping ping/pong, so the socket is demonstrably alive and the watchdog
    must stay quiet. Before the fix this tripped at 90s and fabricated a gap."""
    clock = FakeClock()
    session = FakeSession(last_ping_pong_time=clock.t)
    w = Watchdog(silence_limit=90.0, clock=clock,
                 probe=lambda: socket_liveness(FakeClient(session)))
    for _ in range(60):                 # ten minutes, pings every 10s
        clock.advance(10)
        session.last_ping_pong_time = clock.t
        assert w.is_silent() is False


def test_a_socket_that_stops_pinging_still_trips_the_watchdog():
    """The fix must not disarm the watchdog. Pings stop, silence accrues."""
    clock = FakeClock()
    session = FakeSession(last_ping_pong_time=clock.t)
    w = Watchdog(silence_limit=90.0, clock=clock,
                 probe=lambda: socket_liveness(FakeClient(session)))
    clock.advance(91)                   # stamp left behind
    assert w.is_silent() is True
    assert w.silent_for() == pytest.approx(91.0)


def test_events_still_count_as_liveness_without_a_probe():
    """A watchdog with no probe keeps the original beat-driven behaviour."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    clock.advance(89)
    w.beat()
    clock.advance(89)
    assert w.is_silent() is False


def test_the_newer_of_beat_and_ping_wins():
    """Liveness is the most recent evidence from either source, so a burst of
    events keeps the socket alive even if the ping stamp is older."""
    clock = FakeClock()
    session = FakeSession(last_ping_pong_time=clock.t)
    w = Watchdog(silence_limit=90.0, clock=clock,
                 probe=lambda: socket_liveness(FakeClient(session)))
    clock.advance(80)
    w.beat()                            # an event arrives; ping stamp is stale
    clock.advance(80)
    assert w.is_silent() is False


def test_socket_liveness_reads_the_sdk_ping_stamp():
    assert socket_liveness(FakeClient(FakeSession(1234.5))) == 1234.5


def test_socket_liveness_is_none_when_the_sdk_offers_nothing():
    """Before connect, and on any SDK that stops exposing the stamp, the probe
    must return None so the watchdog falls back to beats rather than treating
    a missing reading as 'alive'. A liveness probe that fails open would defeat
    the watchdog entirely."""
    assert socket_liveness(FakeClient(FakeSession(None))) is None
    assert socket_liveness(FakeClient(None)) is None
    assert socket_liveness(object()) is None
