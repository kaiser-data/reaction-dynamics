"""live_events.jsonl -> the same corpus shape GitHub produces.

    python seed/live_to_corpus.py
    python seed/shapes.py --corpus seed/corpus_slack.json --window 0.5

The point of this file is that there is no second classifier. Live Slack and
ten years of GitHub go through one schema and one seed/shapes.py, so "cascade"
means the same thing in both, and the only difference is the timescale -- which
is the finding, not a bug to normalise away.

Reaction timestamps here come from `reaction_added.event_ts`, captured live.
Rerunning this is free; it only reads the JSONL.
"""

import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(HERE, "live_events.jsonl")
OUT = os.path.join(HERE, "corpus_slack.json")


def iso(ts):
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except Exception:
        return None


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else EVENTS
    out_path = sys.argv[2] if len(sys.argv) > 2 else OUT

    if not os.path.exists(src):
        print(f"no events at {src} -- is seed/listen_slack.py running?")
        sys.exit(1)

    messages, reactions = {}, []
    for line in open(src):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue          # a torn last line after a kill; skip it, keep the rest
        if e["kind"] == "message":
            messages[(e["channel"], e["ts"])] = e
        elif e["kind"] == "reaction_added":
            reactions.append(e)
        elif e["kind"] == "reaction_removed":
            # A removal invalidates the matching add. Keeping both would let a
            # retracted reaction inflate a cascade.
            reactions = [r for r in reactions
                         if not (r["user_id"] == e["user_id"]
                                 and r["emoji"] == e["emoji"]
                                 and r["message_ts"] == e["message_ts"])]

    by_msg = {}
    for r in reactions:
        by_msg.setdefault((r["channel"], r["message_ts"]), []).append({
            "user": r["user"],
            "emoji": r["emoji"],
            "ts": r["ts_iso"] or iso(r["event_ts"]),
        })

    # A reaction can land on a message posted before the listener started. We
    # keep it -- the message text is unknown but the arrival shape is intact,
    # and the shape is what we classify.
    threads = {}
    keys = set(messages) | set(by_msg)
    for chan, ts in keys:
        m = messages.get((chan, ts))
        thread_id = f"slack/{chan}#{ts}"
        threads[thread_id] = {
            "thread_id": thread_id,
            "source": "slack",
            "channel": chan,
            "title": (m["text"][:80] if m else "(posted before listener started)"),
            "url": "",
            "state": "",
            "messages": [{
                "msg_id": f"{chan}:{ts}",
                "thread_id": thread_id,
                "user": m["user"] if m else "unknown",
                "ts": (m["ts_iso"] if m else iso(ts)) or iso(ts),
                "text": m["text"] if m else "",
                "is_root": True,
                "parent_id": None,
                "mentions": [],
                "reactions": sorted(by_msg.get((chan, ts), []),
                                    key=lambda r: r["ts"] or ""),
            }],
        }

    corpus = {
        "source": "slack",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threads": list(threads.values()),
    }
    with open(out_path, "w") as f:
        json.dump(corpus, f, indent=1)

    n_r = sum(len(m["reactions"]) for t in corpus["threads"] for m in t["messages"])
    reacted = sum(1 for t in corpus["threads"] for m in t["messages"] if m["reactions"])
    print(f"{len(corpus['threads'])} messages, {reacted} with reactions, "
          f"{n_r} reactions -> {out_path}")
    if n_r:
        print(f"  every one carries a timestamp. Now: "
              f"python seed/shapes.py --corpus {out_path} --window 0.5")


if __name__ == "__main__":
    main()
