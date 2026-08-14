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
    ":rocket: *REACT TO THIS MESSAGE. RIGHT NOW.* :rocket:\n\n"
    "Anything you like — :thumbsup: :fire: :heart: :clap: :joy: :eyes:\n\n"
    "It is on the projector. You will watch yourself land on it within two "
    "seconds, and you will watch this room's *shape* form live.\n\n"
    "Did we all copy the first person, or did we each decide on our own? "
    "That is the entire experiment, and you are it. :test_tube:\n\n"
    "_Was the hackathon good?_ :clap: = yes  ·  :sleeping: = I need a nap first"
)

w = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
print(w.chat_postMessage(channel=sys.argv[1], text=TEXT)["ts"])
