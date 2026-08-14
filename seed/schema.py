"""The one corpus shape. GitHub and Slack both normalise into this.

Deliberately dependency-free -- no cognee, no Qdrant, no network. The metrics
layer reads this and nothing else, so a graph failure cannot take the product
down with it.

    thread   = an issue (GitHub) or a channel thread (Slack)
    message  = the issue body, a comment, or a Slack message
    reaction = one person, one emoji, one timestamp

The last one is the whole point. Slack's Web API returns reactions as
{name, users, count} -- no per-reaction timestamp, no ordering. So `ts` is None
for every batch-loaded Slack reaction and populated for every GitHub one. That
asymmetry is not a bug to paper over; it is the argument.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Reaction:
    user: str
    emoji: str           # normalised: "+1", "-1", "heart", "eyes", ...
    ts: Optional[str]    # ISO 8601, or None when the source cannot say


@dataclass
class Message:
    msg_id: str
    thread_id: str
    user: str
    ts: str              # ISO 8601
    text: str
    is_root: bool = False
    parent_id: Optional[str] = None
    mentions: list = field(default_factory=list)
    reactions: list = field(default_factory=list)


@dataclass
class Thread:
    thread_id: str
    source: str          # "github" | "slack"
    channel: str         # repo full_name, or Slack channel name
    title: str
    url: str
    state: str           # "open" | "closed" | ""
    messages: list = field(default_factory=list)


def to_dict(obj):
    return asdict(obj)


# --- the shape, as text, for anything that needs to read it without importing --
EXAMPLE = {
    "source": "github",
    "fetched_at": "2026-08-14T18:00:00Z",
    "threads": [
        {
            "thread_id": "microsoft/vscode#519",
            "source": "github",
            "channel": "microsoft/vscode",
            "title": "Add support for X",
            "url": "https://github.com/microsoft/vscode/issues/519",
            "state": "open",
            "messages": [
                {
                    "msg_id": "microsoft/vscode#519:issue",
                    "thread_id": "microsoft/vscode#519",
                    "user": "someone",
                    "ts": "2016-07-04T09:00:00Z",
                    "text": "...",
                    "is_root": True,
                    "parent_id": None,
                    "mentions": ["maintainer"],
                    "reactions": [
                        {"user": "kohlikohl", "emoji": "+1", "ts": "2016-07-04T09:21:52Z"}
                    ],
                }
            ],
        }
    ],
}
