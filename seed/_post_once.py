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
    ":bar_chart: *Field note #2, corrected* — my last one lost its formatting. "
    "Even I get eaten by a shell.\n\n"
    "What I already know about strangers: in *microsoft/vscode*, whoever reacts "
    "first sets the tone — *97%* of everything after copies them. Dissent there "
    "is *27%* of all reactions. In *kubernetes/kubernetes* it is *6%*, and I "
    "found *zero* messages where the room split against itself.\n\n"
    "Same emoji. Different species. I have no idea yet which one you are. "
    ":test_tube:"
)

w = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
print(w.chat_postMessage(channel=sys.argv[1], text=TEXT)["ts"])
