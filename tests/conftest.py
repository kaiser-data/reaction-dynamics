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
