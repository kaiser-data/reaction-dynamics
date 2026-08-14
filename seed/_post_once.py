"""Ad-hoc poster. Use this instead of `python3 -c` with backticks in the text --
the shell substitutes them and you silently post a message with holes in it."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for line in open(os.path.join(os.path.dirname(HERE), ".env")):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from slack_sdk import WebClient  # noqa: E402

TEXT = (
    ":popcorn: *It is 20:20.* Somewhere in this room a README is being written "
    "in a blind panic. Somewhere else, an API key just expired. We have all "
    "been both of those people.\n\n"
    "So, the only survey that matters :point_down: *react with the one you "
    "believe in.* No wrong answers, no scoring, purely for the vibes:\n\n"
    ":coffee:  — caffeine is the actual tech stack\n"
    ":memo:  — writing the submission early beats one more feature\n"
    ":fire:  — one demo that works > five that sort of do\n"
    ":see_no_evil:  — cutting scope at 19:00 saved my life\n"
    ":hourglass_flowing_sand:  — I will be committing at 20:59 and I have made peace with it\n\n"
    "*Bonus round, react with whatever you like:* what is your actual favourite "
    "emoji? And be honest — do we even *need* emoji at work, or would we all "
    "be fine with words? :thinking_face:\n\n"
    "(I am only watching the clock, never the content. Pile on, argue, "
    "contradict each other — genuinely the more chaotic the better for me.) "
    ":heart:"
)

w = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
print(w.chat_postMessage(channel=sys.argv[1], text=TEXT)["ts"])
