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
    assert second["messages"] == 0
    assert second["skipped"] == 2      # the message and the reaction, both known
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
