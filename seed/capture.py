"""Supervised capture daemon.

The listener it replaces had one fatal shape:

    client.connect()
    while True:
        time.sleep(1)

slack_sdk reconnects internally, but if that thread dies the loop sleeps
forever: process alive, exit code 0, tally frozen, nothing captured. A silently
dead listener looks exactly like a quiet Slack, and the timing it misses cannot
be recovered afterwards.

So this module treats SILENCE as failure -- but silence has to be measured from
two sources, not one. Slack events are not enough: a quiet channel produces none
for hours while its socket is healthy, and Socket Mode keepalives are WebSocket
control frames that never reach application code. Liveness is therefore the more
recent of a Slack event and the SDK's polled ping/pong stamp. No traffic on
EITHER means the socket is gone, whatever the process thinks.

Every dark window is written to the gap ledger; no window that was actually
covered is. Both halves matter -- a fabricated gap discredits the ledger as
surely as a missing one.

    .venv/bin/python seed/capture.py --channel C0XXXXXXXXX
"""

import argparse
import os
import queue
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import store                                              # noqa: E402
from listen_slack import (load_env, message_payload,      # noqa: E402
                          reaction_payload)

SILENCE_LIMIT = float(os.getenv("CAPTURE_SILENCE_LIMIT", "90"))

# How long after a connect attempt to ignore `is_connected() == False`. The SDK
# reports False during the handshake, and tripping on that would churn: open a
# gap, reconnect, open a gap, once a second, on a socket that is coming up fine.
CONNECT_GRACE = float(os.getenv("CAPTURE_CONNECT_GRACE", "5"))


def socket_liveness(client):
    """The last moment the SDK saw this socket alive, or None if unknown.

    Socket Mode keepalives are WebSocket PING/PONG *control frames*. slack_sdk
    handles them inside the connection: they never reach
    `socket_mode_request_listeners` or `message_listeners`, so no listener can
    observe them. What the SDK does do is stamp `last_ping_pong_time`, and that
    stamp is the only ping-level liveness signal available to us.

    Returns None rather than a guess when the reading is unavailable — before
    connect, or on an SDK that stops exposing it. A liveness probe that failed
    open would silently disarm the watchdog, which is worse than not having one.
    """
    session = getattr(client, "current_session", None)
    stamp = getattr(session, "last_ping_pong_time", None)
    return float(stamp) if stamp else None


def socket_connected(client):
    """Whether the SDK says this socket is up: True, False, or None for unknown.

    Distinct from socket_liveness, which is a *timeout* and must wait out the
    silence limit before it will say anything. This reading is available at
    once, so a disconnect the SDK already knows about need not cost 90s of
    dark time before it is acted on.

    None, never a guess, when the reading is unavailable — before connect, or
    on an SDK that stops exposing `is_connected`. Unknown is not down: guessing
    False here would reconnect in a loop forever.
    """
    fn = getattr(client, "is_connected", None)
    if not callable(fn):
        return None
    try:
        return bool(fn())
    except Exception:
        return None


class Watchdog:
    """Declares the connection dead after `silence_limit` seconds without traffic.

    Liveness comes from two independent sources, and the most recent wins:

      - `beat()`, called when a Slack event arrives;
      - `probe()`, the SDK's ping/pong stamp (see socket_liveness).

    Events alone are NOT sufficient. A quiet channel produces no events for
    hours while its socket is perfectly healthy, and a watchdog watching only
    events cannot tell that apart from a dead socket — it trips, reconnects, and
    writes a gap for a window it was never actually dark for. Fabricating gaps
    discredits the ledger as surely as missing them.

    Separately, `connected()` reports an explicit disconnect (see
    socket_connected). Silence is a timeout and costs the full limit before it
    speaks; an explicit disconnect is knowable at once and is acted on at once.
    The two are independent trip conditions — either one means reconnect.

    The clock is injected so the behaviour can be tested without waiting.
    """

    def __init__(self, silence_limit=SILENCE_LIMIT, clock=time.time, probe=None,
                 connected=None, connect_grace=CONNECT_GRACE):
        self.silence_limit = silence_limit
        self.clock = clock
        self.probe = probe
        self.connected = connected
        self.connect_grace = connect_grace
        self._last = clock()
        self._connected_at = None

    def beat(self):
        """Called when a Slack event arrives. Pings do not reach here."""
        self._last = self.clock()

    def mark_connected(self):
        """Stamp a successful connect: counts as traffic, and opens the grace
        window during which `is_down()` stays quiet while the socket comes up."""
        self._connected_at = self.clock()
        self.beat()

    def is_down(self):
        """True only when the SDK explicitly says the socket is down.

        Every other answer is False, deliberately. No probe, no connect yet,
        an unknown reading, a raising probe — none of those are evidence of a
        disconnect, and treating them as one would reconnect in a loop and fill
        the ledger with gaps that never happened. When this cannot tell, the
        silence timeout still catches the failure; it just takes longer.
        """
        if self.connected is None or self._connected_at is None:
            return False
        if self.clock() - self._connected_at < self.connect_grace:
            return False
        try:
            return self.connected() is False
        except Exception:
            return False

    def _last_alive(self):
        latest = self._last
        if self.probe is not None:
            stamp = self.probe()
            if stamp is not None and stamp > latest:
                latest = stamp
        return latest

    def silent_for(self):
        return self.clock() - self._last_alive()

    def is_silent(self):
        return self.silent_for() > self.silence_limit


def reconcile_startup(conn, channel, now):
    """Account for the window since this channel was last known to be covered.

    Three cases, and the difference between them matters:
      - never captured before  -> no gap. Absence of history is not a gap.
      - a previous run left an open gap -> it crashed; close it and say so.
      - we have a last_seen -> everything since then was dark. Record it.
    """
    result = {"gap_recorded": False, "dark_seconds": 0.0}

    for gap in store.open_gaps(conn):
        conn.execute("UPDATE capture_gaps SET reason='crash' WHERE id=?",
                     (gap["id"],))
        store.close_gap(conn, gap["id"], now)
        result["gap_recorded"] = True

    seen = store.last_seen(conn, channel)
    if seen is None:
        return result

    dark = now - seen
    if dark > 0:
        store.close_gap(conn, store.open_gap(conn, channel, seen, "cold_start"), now)
        result["gap_recorded"] = True
        result["dark_seconds"] = dark
    return result


def _resolver(web, conn, names, work, stop):
    """Resolve user ids to display names OFF the capture path.

    users_info is an HTTP round-trip. Doing it inside the event handler puts a
    network call in the middle of exactly the reaction bursts worth measuring.
    """
    while not stop.is_set():
        try:
            uid = work.get(timeout=0.5)
        except queue.Empty:
            continue
        if not uid or uid in names:
            continue
        try:
            u = web.users_info(user=uid)["user"]
            names[uid] = u.get("real_name") or u.get("name") or uid
        except Exception:
            names[uid] = uid          # visibly a raw id, never a fabricated name
        conn.execute("UPDATE events SET user=? WHERE user_id=? AND user IS NULL",
                     (names[uid], uid))
        conn.execute("UPDATE messages SET user=? WHERE user_id=? AND user IS NULL",
                     (names[uid], uid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="channel id to capture")
    ap.add_argument("--db", default=None, help="path, or a libsql:// URL")
    ap.add_argument("--silence-limit", type=float, default=SILENCE_LIMIT,
                    help="seconds of socket silence before declaring it dead")
    args = ap.parse_args()

    load_env()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not app_token.startswith("xapp-") or not bot_token.startswith("xoxb-"):
        print("SLACK_APP_TOKEN (xapp-) and SLACK_BOT_TOKEN (xoxb-) are both "
              "required. See README 'Capture a live Slack room'.")
        sys.exit(1)

    conn = store.connect(args.db)
    store.init(conn)

    now = time.time()
    rec = reconcile_startup(conn, args.channel, now)
    if rec["gap_recorded"]:
        print(f"  gap recorded: {rec['dark_seconds'] / 60:.1f} min not captured "
              f"before this start")
    else:
        print("  no prior coverage to account for")

    from slack_sdk import WebClient
    from slack_sdk.socket_mode.builtin import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    web = WebClient(token=bot_token)
    auth = web.auth_test()
    print(f"connected as {auth['user']} in {auth['team']}")

    # `client` is replaced on every reconnect, so the probe reads it through
    # this dict rather than closing over a name that goes stale.
    state = {"gap_id": None, "client": None}
    dog = Watchdog(silence_limit=args.silence_limit,
                   probe=lambda: socket_liveness(state["client"]),
                   connected=lambda: socket_connected(state["client"]))
    names, work, stop = {}, queue.Queue(), threading.Event()
    threading.Thread(target=_resolver, args=(web, conn, names, work, stop),
                     daemon=True).start()

    # Counters reflect the capture, not this process. A restart must not make
    # the tally read as though the room went quiet.
    counts = {"reaction_added": 0, "reaction_removed": 0, "message": 0}
    for row in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM events WHERE channel=? GROUP BY kind",
            (args.channel,)).fetchall():
        counts[row["kind"]] = row["n"]
    counts["message"] = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE channel=?",
        (args.channel,)).fetchone()["n"]

    def handle(client, req: SocketModeRequest):
        # Ack first, always. Slack retries anything unacked within 3s.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        dog.beat()
        store.touch_heartbeat(conn, args.channel, time.time())
        if req.type != "events_api":
            return
        e = req.payload.get("event", {})

        p = reaction_payload(e)
        if p and p.get("channel") == args.channel:
            if store.record_event(conn, e["type"], p):
                counts[e["type"]] += 1
                work.put(p["user_id"])
            return

        p = message_payload(e)
        if p and p.get("channel") == args.channel:
            if store.record_message(conn, p):
                counts["message"] += 1
                work.put(p["user_id"])

    def connect_client():
        c = SocketModeClient(app_token=app_token, web_client=web)
        c.socket_mode_request_listeners.append(handle)
        c.connect()
        state["client"] = c
        dog.mark_connected()
        return c

    client = connect_client()
    print(f"capturing {args.channel} -> {args.db or store.DEFAULT_PATH}")
    print(f"  watchdog: reconnects at once on an explicit disconnect, "
          f"or after {args.silence_limit:.0f}s of socket silence")
    print("  every dark window is recorded. Ctrl-C to stop.\n")

    def bye(*_):
        if state["gap_id"] is not None:
            store.close_gap(conn, state["gap_id"], time.time())
        store.touch_heartbeat(conn, args.channel, time.time(), inc=0)
        stop.set()
        print(f"\nstopped. {counts['reaction_added']} reactions captured. "
              f"{store.total_dark_seconds(conn) / 60:.1f} min dark on record.")
        sys.exit(0)

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)

    backoff = 1.0
    while True:
        time.sleep(1)
        # Two independent trip conditions. `is_down()` is the fast one: when the
        # SDK already knows the socket dropped, waiting out the silence limit
        # would just be 90s of dark time we could have been reconnecting through.
        down = dog.is_down()
        if down or dog.is_silent():
            if state["gap_id"] is None:
                # Backdated to the last moment coverage can be positively
                # accounted for -- which, on an explicit disconnect, is seconds
                # ago rather than the full limit.
                dark_for = dog.silent_for()
                reason = "disconnected" if down else "watchdog_silence"
                state["gap_id"] = store.open_gap(
                    conn, args.channel, time.time() - dark_for, reason)
                why = ("socket reports disconnected" if down
                       else f"{dark_for:.0f}s of socket silence")
                print(f"\n  !! {why} -- reconnecting. "
                      f"Gap {state['gap_id']} open.")
            try:
                client.close()
            except Exception:
                pass
            try:
                client = connect_client()
                store.close_gap(conn, state["gap_id"], time.time())
                print(f"  reconnected. Gap {state['gap_id']} closed.")
                state["gap_id"], backoff = None, 1.0
            except Exception as exc:
                print(f"  reconnect failed ({type(exc).__name__}); "
                      f"retrying in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        else:
            print(f"\r  up  reactions {counts['reaction_added']:>4}  "
                  f"messages {counts['message']:>4}  "
                  f"quiet {dog.silent_for():>4.0f}s", end="", flush=True)


if __name__ == "__main__":
    main()
