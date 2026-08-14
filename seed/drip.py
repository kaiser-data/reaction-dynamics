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

# Hackathon-themed, in the field-anthropologist voice. Every joke carries a real
# number from the corpus where it can -- the humour is the delivery mechanism for
# the pitch, not a break from it. Alternating easy/contested so the corpus gets
# both trickles and splits.
POOL = [
    ":stopwatch: *Status check, no judgement, I am literally incapable of judgement.*\n"
    ":rocket: shipping  ·  :fire: debugging  ·  :melting_face: rewriting from scratch  "
    "·  :coffee: pretending to work",

    ":innocent: *Confession booth.* How much of your demo is hardcoded right now?\n"
    ":innocent: none  ·  :grimacing: some  ·  :skull: all of it  ·  "
    ":shushing_face: define hardcoded",

    ":pray: *Demo gods.* React :pray: if your demo has worked on the first try "
    "tonight. React :skull: if you just realised you have not tested it on the "
    "projector.",

    ":brain: *Contested, and I want the split:* the knowledge graph is the easy "
    "part -- getting clean input is the whole job.\n"
    ":white_check_mark: agree  ·  :x: disagree",

    ":trophy: *Real MVP of tonight:*\n"
    ":coffee: caffeine  ·  :pizza: pizza  ·  :signal_strength: the wifi holding  "
    "·  :headphones: noise cancelling  ·  :people_hugging: whoever brought a power strip",

    ":red_circle: *Awkward question for a room with two sponsors in it:* which one "
    "is doing the real work in your stack tonight?\n"
    ":red_circle: the vector DB  ·  :spider_web: the graph  ·  :shrug: honestly both  "
    "·  :sweat_smile: neither, it is one big prompt",

    ":clock8: *Hackathon law:* the last 30 minutes are worth more than the first "
    "two hours.\n:white_check_mark: true  ·  :x: cope",

    ":eyes: I can see that some of you are reading and not reacting. I am not "
    "judging. I am *measuring*, which is different, and arguably worse.\n"
    "React :eyes: to be counted among the lurkers. It is a valid dialect.",

    ":thumbsup: *The actual thesis, put to the room:* when someone :thumbsup: your "
    "message, do they mean _done_ or _seen_?\n"
    ":white_check_mark: done  ·  :eyes: seen  ·  :shrug: depends entirely who sent it",

    ":test_tube: *Field note.* In `vscode` a quarter of all reactions are dissent. "
    "In `kubernetes` it is 6 percent, and I found zero messages where the room "
    "split against itself.\nThis room so far reads as... polite. "
    ":white_check_mark: we are nice  ·  :x: we are conflict-avoidant, which is different",

    ":crystal_ball: *Would you install this?* A bot that told you which of your "
    "messages everyone acknowledged and nobody answered.\n"
    ":white_check_mark: yes  ·  :x: absolutely not  ·  :grimacing: terrifying but yes",

    ":wave: *Last call before I stop talking and start counting.* Whatever you "
    "react to in the next 20 minutes ends up in the graph at 21:15.\n"
    "No names on the slide. Just shapes. :heart:",
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
