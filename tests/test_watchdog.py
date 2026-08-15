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
from capture import (Watchdog, reconcile_startup, socket_connected,
                     socket_liveness)

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
    """An event counts. (Pings count too, but via the probe -- not this path.)"""
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


# ------------------------------------------------------ explicit disconnection
#
# Silence detection is a *timeout*: correct, but it must wait out the full limit
# before it will say anything. When the SDK already knows the socket is down,
# waiting 90s to agree with it means 90s of real dark time that the ledger
# backdates but the capture cannot recover. An explicit disconnect is knowable
# at once, so it is acted on at once.
#
# The hazard is the mirror image of the ping bug: over-trusting this signal.
# `is_connected()` is False during the handshake too, and a watchdog that tripped
# on that would churn -- open a gap, reconnect, open a gap -- every second, on a
# socket that is coming up perfectly normally.


class FakeConnState:
    """Stands in for a client whose connection state the test drives."""

    def __init__(self, connected):
        self.connected = connected

    def __call__(self):
        return self.connected


def test_an_explicit_disconnect_trips_immediately():
    """The whole point: no waiting out the silence limit."""
    clock = FakeClock()
    conn_state = FakeConnState(True)
    w = Watchdog(silence_limit=90.0, clock=clock, connected=conn_state)
    w.mark_connected()
    clock.advance(10)                   # past the grace window, far short of 90s
    assert w.is_down() is False

    conn_state.connected = False
    assert w.is_down() is True
    assert w.is_silent() is False       # silence alone would still say nothing


def test_the_handshake_window_does_not_count_as_down():
    """is_connected() is False while connecting. Tripping on that would churn."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock, connected=FakeConnState(False),
                 connect_grace=5.0)
    w.mark_connected()
    assert w.is_down() is False         # grace window, still coming up
    clock.advance(3)
    assert w.is_down() is False
    clock.advance(3)                    # 6s: grace expired, still not connected
    assert w.is_down() is True


def test_an_unknown_connection_state_is_not_treated_as_down():
    """Same rule as the liveness probe: unknown is never a guess. A future SDK
    that drops is_connected() must degrade to silence-only detection, not
    manufacture a disconnect every second."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock, connected=lambda: None)
    w.mark_connected()
    clock.advance(10)
    assert w.is_down() is False


def test_a_raising_is_connected_is_not_treated_as_down():
    """A probe that throws must not be read as a disconnect."""
    def boom():
        raise RuntimeError("SDK internals moved")

    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock, connected=boom)
    w.mark_connected()
    clock.advance(10)
    assert w.is_down() is False


def test_without_a_connected_probe_nothing_changes():
    """The parameter is optional; omitting it leaves silence-only behaviour."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    clock.advance(10)
    assert w.is_down() is False


def test_is_down_stays_false_before_the_first_connect():
    """Startup coverage is reconcile_startup's job, not the watchdog's. Before
    a connect has been marked there is nothing for this to be down *from*."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock, connected=FakeConnState(False))
    clock.advance(10)
    assert w.is_down() is False


def test_reconnecting_restarts_the_grace_window():
    """Every connect gets its own handshake window, not just the first."""
    clock = FakeClock()
    conn_state = FakeConnState(False)
    w = Watchdog(silence_limit=90.0, clock=clock, connected=conn_state,
                 connect_grace=5.0)
    w.mark_connected()
    clock.advance(10)
    assert w.is_down() is True          # first connect failed to come up

    w.mark_connected()                  # reconnect attempt
    assert w.is_down() is False         # fresh grace window
    clock.advance(10)
    assert w.is_down() is True


def test_mark_connected_also_counts_as_liveness():
    """A successful connect is traffic; it must clear accumulated silence."""
    clock = FakeClock()
    w = Watchdog(silence_limit=90.0, clock=clock)
    clock.advance(89)
    w.mark_connected()
    clock.advance(89)
    assert w.is_silent() is False


# --------------------------------------------------------- SDK contract checks
#
# Everything above runs against stubs, which is what keeps this suite instant and
# dependency-free. That is also exactly how the ping bug survived: stubs agree
# with whatever the code believes about the SDK. These two cases check the real
# slack_sdk instead, and skip where it is not installed -- so the suite stays
# runnable without the `live` extra, but drift is caught wherever it can be.


def test_the_sdk_still_exposes_is_connected():
    """If an upgrade removes this, socket_connected degrades to None and the
    fast disconnect path silently stops working -- capture still correct, just
    slower. Better to see it fail here than to wonder later why gaps got longer.
    """
    pytest.importorskip("slack_sdk")
    from slack_sdk.socket_mode.builtin import SocketModeClient
    assert callable(getattr(SocketModeClient, "is_connected", None))


def test_an_unconnected_sdk_client_reads_as_unknown_not_down():
    """The real client raises AttributeError before connect (no current_session).
    socket_connected must absorb that into None. If it ever returned False here,
    the daemon would open a gap and reconnect once a second from startup."""
    pytest.importorskip("slack_sdk")
    from slack_sdk.socket_mode.builtin import SocketModeClient
    fresh = SocketModeClient.__new__(SocketModeClient)   # no network, no creds
    assert socket_connected(fresh) is None
