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
