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
    "<!channel> *One click, then I go quiet* :point_down:\n\n"
    "React to *this message* with anything at all. A :thumbsup:, a :fire:, "
    "a :eyes:, something rude. It genuinely does not matter which.\n\n"
    "*How it works, in two lines:* I am not reading what you say — only "
    "*when* you say it. Slack's API can tell you that a message has 4 "
    ":thumbsup:, but it can *never* tell you in which order they arrived or "
    "how long apart. That only exists in the live event, for the instant it "
    "happens. If nobody is listening at that moment, it is gone forever.\n\n"
    "So: four people reacting in six seconds is a *cascade* — you copied "
    "whoever went first. Four people spread over an hour is a *trickle* — you "
    "each decided on your own. Same four emoji. Opposite meanings. Only the "
    "timing can tell them apart.\n\n"
    "At *21:15* I will show you the shape this room just made. It will be the "
    "one thing on that stage that could not have been faked in advance, "
    "because you are making it right now.\n\n"
    "Nothing is scored. Nobody is ranked. I could not tell you who is "
    "\"best\" if I wanted to — I only have clocks. :test_tube:"
)

w = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
print(w.chat_postMessage(channel=sys.argv[1], text=TEXT)["ts"])
