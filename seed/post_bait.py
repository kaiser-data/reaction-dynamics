"""Seed the lab channel with things worth reacting to.

    python seed/post_bait.py C0XXXXXXXXX

Each message is chosen to produce a different arrival shape. A channel of
agreeable statements yields nothing but trickles -- the split shape only exists
if something is genuinely contested, so half of these are real disagreements.

The first message is a consent notice. It is not decoration: everyone whose
reactions end up in the graph should know that is happening, and being able to
say so on stage is worth more than any disclaimer slide.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

MESSAGES = [
    # consent first, before anything is captured
    ":wave: *This channel is a live experiment for the Cognee x Qdrant hack night.*\n"
    "We're measuring *when* people react, not what they feel. Reaction _timing_ "
    "exists only in Slack's live events -- no export contains it -- so we capture "
    "it as it happens. Reactions in here may show up in our demo at 21:15, as "
    "arrival patterns. No sentiment, no scoring, nobody ranked. React away, or "
    "don't :slightly_smiling_face:",

    ":point_down: Baseline. React with anything at all so we have a floor to "
    "compare against.",

    "*Tabs or spaces?* :white_check_mark: tabs  ·  :x: spaces",

    "*Vector DB of choice:* :red_circle: Qdrant  ·  :elephant: pgvector  ·  "
    ":shrug: whatever ships",

    "*Hot take:* AI writes the majority of production code within two years.\n"
    ":white_check_mark: agree  ·  :x: disagree",

    "*Slack's :thumbsup: means \"I've seen it\", not \"I've done it\".*\n"
    ":white_check_mark: true in my team  ·  :x: not in mine",

    "Genuinely asking: has anyone here got `reaction_added` working over Socket "
    "Mode before? :eyes:",
]


def main():
    channel = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SLACK_TEST_CHANNEL")
    if not channel:
        print("usage: python seed/post_bait.py <channel_id>")
        sys.exit(1)

    for line in open(os.path.join(os.path.dirname(HERE), ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    from slack_sdk import WebClient
    w = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

    for i, text in enumerate(MESSAGES, 1):
        r = w.chat_postMessage(channel=channel, text=text)
        print(f"  {i}/{len(MESSAGES)}  ts={r['ts']}")
        # A pause so the messages do not all share a timestamp. A product about
        # timing, demoed on messages posted in the same second, would be a bad look.
        time.sleep(2)

    print(f"\nposted {len(MESSAGES)} messages to {channel}")


if __name__ == "__main__":
    main()
