# LinkedIn post — draft

**Source format is plain Markdown on purpose.** LinkedIn strips Markdown, so if you
want bold you swap letters for Unicode math glyphs at paste time — never in this
file. Glyphs here would break grep, diff and spellcheck, and any restyle would mean
a rewrite.

**Styling directive (apply at paste time, not here):**

- Bold the three section labels only: `The insight`, `What it does`, `What I got wrong`
- Bold exactly one number: **32.7 hours**
- Leave everything else plain — especially names, hashtags and the URL
- Budget: styled chars cost 2× against LinkedIn's 3000 limit. Draft A is ~1,750
  plain; the styling above adds ~60. Comfortable.

---

## Draft A — the main one (~1,750 chars)

Slack can tell you that four people reacted 👍.

It cannot tell you who went first.

That sounds like a detail. It isn't. Four people reacting in six seconds is a room
copying whoever moved first. Four people reacting over an hour is four people
deciding independently. Same emoji, same count, opposite meanings — and the second
one is the only one that's actually agreement.

The insight

Slack's Web API returns reactions as {name, users, count}. No timestamps. No
ordering. That information exists only in the live event, at the instant it
happens. If nothing is listening, it is gone permanently — no export, no search,
no backfill will ever recover it.

So I spent the hack night building the listener first and the product second.

What it does

Reaction Dynamics classifies how reactions arrive in time, into four shapes:

• cascade — a burst after the first one. Social proof, not agreement.
• trickle — even and independent. The trustworthy kind.
• stall then burst — silence, then everyone at once. Deference.
• split — opposed emoji interleaved. A live argument the final count hides.

Classification is a Kolmogorov-Smirnov test against uniform arrival, not a
threshold I picked by eye.

Across 36,779 timestamped GitHub reactions, the median cascade takes 32.7 hours.
The six-second cascade everyone pictures simply isn't in the largest public
corpus that exists. It only happens in a live room, and only if you were
listening.

Two repos, same emoji, different languages: in microsoft/vscode, 27% of all
reactions are dissent and 12% of messages are active disagreement. In
kubernetes/kubernetes it's 6%, with no splits at all. One is a contested room.
One is a consensus room. The 👍 means something different in each.

What I got wrong

The shape comparison rests on 21 classified Kubernetes messages against 211 for
vscode. Suggestive, not established, and I said so on the slide before anyone
could ask.

I also nearly shipped a claim of "microsecond precision" that wasn't true — Slack's
event_ts fraction is a sequence counter, not a clock. What you actually get is
strict ordering, which is what the analysis needs. I caught it fifteen minutes
before presenting, by explicitly auditing my own numbers.

Reactions are not outcomes. This is a triage signal, not a decision driver.
Nobody is ranked. It exists to prevent a misunderstanding, not to score a person.

First place at the Cognee x Qdrant Hack Night in Berlin. Thanks to both teams —
notes on what I'd improve in the stack are in the repo.

github.com/kaiser-data/reaction-dynamics

#KnowledgeGraphs #VectorSearch #Slack #DataEngineering

---

## Draft B — short version (~700 chars)

Slack can tell you that four people reacted 👍. It cannot tell you who went first.

Four reactions in six seconds is a room copying whoever moved first. Four over an
hour is four people deciding independently. Same count, opposite meanings.

That timing exists only in the live event. No export contains it. If nothing is
listening, it's gone.

So I built the listener first. It sorts reactions into four arrival shapes —
cascade, trickle, stall-then-burst, split — using a KS test rather than a
threshold picked by eye.

Across 36,779 timestamped GitHub reactions the median cascade takes 32.7 hours.
The six-second version isn't in the archive at all. It only happens live.

First place at the Cognee x Qdrant Hack Night, Berlin.

github.com/kaiser-data/reaction-dynamics

---

## Notes on the choices

**The hook is the whole post.** Line 1 is all that shows before "see more", so it
carries a concrete, falsifiable claim rather than "excited to share". The win is
mentioned at the end, not the start — the insight earns the attention, and the
placement reads as confidence rather than announcement.

**"What I got wrong" is deliberate.** Naming the n=21 limit and the near-miss on
"microsecond precision" is what makes the other numbers credible. It's also the
section most likely to start a real conversation in the comments, which is what
actually drives reach.

**No styled glyphs on the hashtags or the URL** — a styled hashtag indexes as
nothing, and a styled URL can't be copied.

**Repo link, not a Netlify link.** The repo contains everything including the live
site link, and it's the artifact people in this audience will actually open.
