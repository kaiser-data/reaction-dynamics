"""Store tests. Every test runs against an in-memory database — no file, no
network, no Slack. A green run here says nothing about the live capture path."""

import json

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
