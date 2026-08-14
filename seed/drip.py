"""Post one reaction-bait message every N minutes until told to stop.

    python seed/drip.py C0BQ7FGF82H --every 10

A drip beats a dump. Nine messages posted in one minute all compete for the same
reactions and every latency measurement is degenerate -- you cannot demo a
product about timing on data where everything happened at once. Spreading posts
across the evening produces real time-to-first-reaction variation, and gives
people arriving later something fresh at the top of the channel.

Messages are ordered to alternate: agreeable, contested, agreeable, contested.
The split shape only exists if something is genuinely disputed.
"""

import argparse
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

POOL = [
    "*Quick one:* is `main` the right default branch name?\n"
    ":white_check_mark: yes  ·  :x: don't care  ·  :eyes: still typing master",

    "Who else is running out of laptop battery right now :battery:",

    "*Contested:* vector search has basically solved retrieval.\n"
    ":white_check_mark: agree  ·  :x: absolutely not",

    ":rocket: React if your demo currently works. :fire: React if it works "
    "*and* you've rehearsed it.",

    "*Spicy:* most RAG pipelines would be better as a single well-chosen SQL query.\n"
    ":white_check_mark: true  ·  :x: nonsense",

    "Anyone else find `:thumbsup:` gets used to mean \"I'm ignoring this politely\"? "
    ":thumbsup:",

    "*Real question, genuinely asking:* has anyone got a Slack export with more "
    "than 50 reacted messages in it? Could use it. :pray:",

    "*Contested:* three-hour hackathons produce better ideas than three-week ones.\n"
    ":white_check_mark: yes  ·  :x: no",

    ":coffee: vs :tea: — the only debate that matters at 20:00",

    "*Hot take:* the knowledge graph is the easy part; getting clean input is the "
    "whole job.\n:white_check_mark: agree  ·  :x: disagree",

    "React :eyes: if you're reading this channel but haven't reacted to anything yet. "
    "(Yes, we can tell. That's sort of the point.)",

    "*Last one:* would you install a bot that told you which of your messages "
    "everyone acknowledged but nobody answered?\n"
    ":white_check_mark: yes  ·  :x: absolutely not  ·  :grimacing: terrifying but yes",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--every", type=float, default=10, help="minutes between posts")
    ap.add_argument("--until", default="20:45", help="stop posting after this local time")
    args = ap.parse_args()

    for line in open(os.path.join(os.path.dirname(HERE), ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    from slack_sdk import WebClient
    w = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

    stop_h, stop_m = (int(x) for x in args.until.split(":"))

    for i, text in enumerate(POOL, 1):
        now = datetime.now()
        if (now.hour, now.minute) >= (stop_h, stop_m):
            print(f"reached {args.until}, stopping with {len(POOL) - i + 1} unposted")
            break
        r = w.chat_postMessage(channel=args.channel, text=text)
        print(f"{now:%H:%M:%S}  {i}/{len(POOL)}  ts={r['ts']}", flush=True)
        if i < len(POOL):
            time.sleep(args.every * 60)

    print("drip finished")


if __name__ == "__main__":
    main()
