"""The extracted handlers. These run on raw Slack event dicts, so they can be
tested without Slack -- but note that no test here proves the live socket works."""

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
