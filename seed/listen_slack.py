"""Socket Mode listener. The only way to get a per-reaction timestamp.

    python seed/listen_slack.py                 # run it and leave it running
    python seed/listen_slack.py --channel C123  # restrict to one channel

Slack's Web API returns reactions as {name, users, count} -- no timestamps, no
ordering. The `reaction_added` event carries `event_ts`, which is the moment the
reaction happened. That field exists nowhere else: not in an export, not in
conversations.history, not in any batch tool. If this process is not running when
someone reacts, that timing is gone permanently.

So: start it first, leave it running, never restart it to "fix" something.

Every event is appended to seed/live_events.jsonl and flushed immediately -- a
crash costs you nothing already captured.

Needs in .env:
    SLACK_APP_TOKEN   xapp-...   Basic Information -> App-Level Tokens, connections:write
    SLACK_BOT_TOKEN   xoxb-...   OAuth & Permissions, after installing to the workspace
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "live_events.jsonl")

_names = {}      # user id -> display name, resolved once
_counts = {"reaction_added": 0, "reaction_removed": 0, "message": 0, "other": 0}
_started = time.time()


def load_env():
    """Read .env without adding a dependency. python-dotenv may not be present
    and this script must start on a laptop that has just been cloned."""
    path = os.path.join(os.path.dirname(HERE), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def iso(slack_ts):
    """Slack timestamps are '1360782804.083113' -- seconds since epoch, with
    microseconds. Keep the raw string too; it is the message's identity."""
    try:
        return datetime.fromtimestamp(float(slack_ts), timezone.utc).isoformat()
    except Exception:
        return None


def name_of(client, uid):
    if not uid:
        return "unknown"
    if uid not in _names:
        try:
            u = client.users_info(user=uid)["user"]
            _names[uid] = u.get("real_name") or u.get("name") or uid
        except Exception:
            _names[uid] = uid
    return _names[uid]


def tally():
    mins = (time.time() - _started) / 60
    print(f"\r  up {mins:5.1f}m   reactions {_counts['reaction_added']:>4} "
          f"(-{_counts['reaction_removed']})   messages {_counts['message']:>4}",
          end="", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", help="only record this channel id")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    load_env()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()

    if not app_token.startswith("xapp-"):
        print("SLACK_APP_TOKEN missing or not an xapp- token.\n"
              "  api.slack.com/apps -> your app -> Basic Information ->\n"
              "  App-Level Tokens -> Generate -> scope connections:write")
        sys.exit(1)
    if not bot_token.startswith("xoxb-"):
        print("SLACK_BOT_TOKEN missing or not an xoxb- token.\n"
              "  OAuth & Permissions -> Bot User OAuth Token (install the app first)")
        sys.exit(1)

    from slack_sdk import WebClient
    # The `builtin` implementation is synchronous and uses only the stdlib.
    # The aiohttp one raises "no running event loop" unless the whole program is
    # async, and the default one needs websocket-client installed. Neither is
    # worth a dependency argument on run day.
    from slack_sdk.socket_mode.builtin import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    web = WebClient(token=bot_token)
    auth = web.auth_test()
    print(f"connected as {auth['user']} in {auth['team']} ({auth['team_id']})")

    out = open(args.out, "a", buffering=1)   # line buffered: never lose an event

    def record(kind, payload):
        payload["kind"] = kind
        payload["received_at"] = datetime.now(timezone.utc).isoformat()
        out.write(json.dumps(payload) + "\n")
        _counts[kind] = _counts.get(kind, 0) + 1
        tally()

    def handle(client: SocketModeClient, req: SocketModeRequest):
        # Ack first, always, and before any slow work. Slack retries anything
        # unacked within 3s, which would double-count reactions.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        if req.type != "events_api":
            return
        e = req.payload.get("event", {})
        t = e.get("type")

        if t in ("reaction_added", "reaction_removed"):
            item = e.get("item", {}) or {}
            if args.channel and item.get("channel") != args.channel:
                return
            record(t, {
                # event_ts IS the signal. Nothing else on this line is unique.
                "event_ts": e.get("event_ts"),
                "ts_iso": iso(e.get("event_ts")),
                "user": name_of(web, e.get("user")),
                "user_id": e.get("user"),
                "emoji": e.get("reaction"),
                "item_user": name_of(web, e.get("item_user")),
                "channel": item.get("channel"),
                "message_ts": item.get("ts"),
            })

        elif t == "message" and not e.get("subtype"):
            if args.channel and e.get("channel") != args.channel:
                return
            record("message", {
                "ts": e.get("ts"),
                "ts_iso": iso(e.get("ts")),
                "user": name_of(web, e.get("user")),
                "user_id": e.get("user"),
                "text": e.get("text", ""),
                "channel": e.get("channel"),
                "thread_ts": e.get("thread_ts"),
            })

    client = SocketModeClient(app_token=app_token, web_client=web)
    client.socket_mode_request_listeners.append(handle)

    print(f"listening -> {args.out}")
    print("  reaction_added carries event_ts; nothing else can reconstruct it.")
    print("  leave this running. Ctrl-C to stop.\n")

    def bye(*_):
        print(f"\n\nstopped. {_counts['reaction_added']} reactions captured "
              f"-> {args.out}")
        out.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    client.connect()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
