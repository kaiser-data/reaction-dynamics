# LinkedIn post — drafts and raw material

**Source format is plain Markdown on purpose.** LinkedIn strips Markdown, so if you
want bold you swap letters for Unicode math glyphs at paste time — never in this
file. Glyphs here would break grep, diff and spellcheck, and any restyle would mean
a rewrite.

**Styling directive (apply at paste time, not here):**

- Bold section labels only, two levels maximum
- Bold exactly one number — the one the post rests on
- Leave plain: names, @-mentions, hashtags, URLs
- Styled chars cost 2× against LinkedIn's 3000-unit limit

**How to use this file:** Part 1 is two ready-to-post drafts. Part 2 is a module
library — every angle, number and story that could go in, written as drop-in
blocks. Pick, don't write. Everything in Part 2 is verified; nothing is invented.

---
---

# PART 1 — Ready to post

## Draft A — the main one (~2,616 units, 384 spare)

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

## Draft B — short version (~773 units)

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
---

# PART 2 — Module library

Drop-in blocks. Everything verified against the data or a cited source.

## 2.1 Alternative hooks (pick ONE — line 1 is all that shows before "see more")

**H1 — the current one. Concrete, falsifiable, no jargon.**
> Slack can tell you that four people reacted 👍.
> It cannot tell you who went first.

**H2 — the confession. Highest engagement risk/reward.**
> I spent a hackathon measuring my colleagues' emoji.
> Not what they meant. When they clicked.

**H3 — the reframe. Good if your audience is management-heavy.**
> Most misunderstandings at work aren't disagreements.
> They're translation errors.

**H4 — the number. Works if you want the data crowd.**
> I analysed 36,779 timestamped emoji reactions from ten years of GitHub.
> The six-second pile-on everyone imagines? It isn't in there.

**H5 — the uncomfortable one. Strongest for comments.**
> In one open-source repo, whoever reacts first is copied 97% of the time.
> That's not a team agreeing. That's a team following.

**H6 — the loss. Emotional, very short.**
> There's a signal in your Slack that is deleted the instant it's created.
> Nobody is recording it.

**H7 — generational. Broadest reach, most divisive comments.**
> To half your team, 👍 means "on it".
> To the other half it means "I'm ignoring you politely".

## 2.2 The problem, stated three ways

**Short:**
> Four reactions in six seconds is social proof. Four over an hour is independent
> agreement. Slack shows you "4" for both.

**With the three-rooms framing:**
> Someone posts a decision. Four people hit 👍. Did they all agree? Did they just
> see it? Did one person agree and the other three copy them? The count is
> identical in all three cases — and only one of them is a decision.

**With the stakes:**
> Teams act on the count. They read four 👍 as consensus, ship the thing, and
> discover in the retro that nobody had actually thought about it. The count was
> never the information. The timing was.

## 2.3 Why it can't be backfilled (the moat paragraph)

> Slack's Web API returns reactions as {name, users, count}. No timestamps, no
> ordering. Per-reaction timing exists only in the live reaction_added event, for
> the instant it happens. This isn't an indexing gap someone will close — it's
> excluded by construction. No export, no competitor's corpus, no future feature
> can recover a moment nobody recorded.

Optional sharpener:
> Which means the listener is the first thing you start and the last thing anyone
> can reconstruct.

## 2.4 The four shapes (three lengths)

**Full:**
> • cascade — a burst right after the first. Social proof: the room copied whoever
>   moved first.
> • trickle — even, independent arrivals. The trustworthy kind of agreement.
> • stall then burst — silence, then everyone at once. Deference: they waited to
>   see what one person thought.
> • split — opposed emoji interleaved. A live disagreement the final count hides
>   completely.

**Compressed:**
> cascade (they copied), trickle (they each decided), stall-then-burst (they
> waited), split (they disagreed and the count hid it).

**One-liner:**
> Four shapes, identical counts, opposite meanings.

## 2.5 Method — for the technical audience

> Classification is a hypothesis test, not a threshold picked by eye: arrival
> times are tested against uniform with a one-sample Kolmogorov-Smirnov statistic,
> critical value 1.36/√n. So "trickle" means "we cannot reject independent
> arrival" — a claim with a failure mode, not a vibe.
>
> Burstiness uses Goh & Barabási (2008): B = (σ−μ)/(σ+μ).

**The result that justifies it — strong, underused:**
> My first version used a hand-picked uniformity cutoff. It labelled 67.7% of
> everything "mixed" — the classifier's way of saying "I don't know". Replacing
> the cutoff with the KS test took that to 0%. Same data, same shapes. The
> difference was having a real null hypothesis instead of a number I liked.

## 2.6 Numbers — all verified, pick what fits

**Corpus:**
> 36,779 timestamped reactions · 2,294 messages · 24 threads · 16,725 distinct
> reactors · 2016 → 2026 · GitHub Reactions API, the only public source carrying
> per-reaction identity, content and timestamp together.

**The timescale finding:**
> Median cascade span: 32.7 hours, across 26 cascades. The seconds-scale pile-on
> is not in the archive. It exists only live.

**Time to first reaction:**
> GitHub, ten years: 22.4 minutes median.
> A live room, that night: 4.0 minutes.
> Same classifier. About five times faster. The timescale is the finding, not a
> bug to normalise away.

**The dialect table:**
> microsoft/vscode — 👍 52%, 👎 27%, split shapes 12%, trickle 65%
> kubernetes/kubernetes — 👍 69%, 👎 6%, split shapes 0%, trickle 90%
> One is a contested product-feature room. One is a consensus room.

**Emoji distribution — good standalone stat:**
> Across 36,779 reactions, two glyphs do 86% of the work: 👍 is 67.7%, 👎 is 18.5%.
> Everything else — ❤️ 😄 🎉 😕 🚀 👀 — shares the remaining 14%.
> 👍 is the most overloaded symbol in professional communication, which is exactly
> why it fails. One glyph carries "yes", "seen", "fine" and "I'm done arguing".

**The bellwether:**
> Of everyone who reacted first in vscode, one person was copied 97% of the time
> across 7 occasions. Another, going first just as often, was copied 65%.
> That spread is the interesting part: a room where everyone hits 97% has stopped
> thinking independently. A range of follow rates is a room that still makes up
> its own mind.

**Performance, if you want the engineering crowd:**
> The classifier does roughly 600,000 reactions/second on one core — 232 messages
> classified in under 60ms, pure standard library, no numpy.
> The graph query is 699ms end to end, and I assumed the embedding call dominated.
> I measured: 287ms embedding, 318ms Qdrant round trip. Neither is compute. Both
> are network. I'd have optimised the wrong layer.

## 2.7 The stack

**Short:**
> Slack Socket Mode → one corpus schema → shape classifier → typed DataPoints →
> cognee → Qdrant → a four-tool agent.

**With the reasoning:**
> Findings are stored as typed nodes — ReactionShape, UnansweredAsk, Person, Room
> — so the finding itself is a first-class object with edges to the message, the
> room and the person who moved first. Not a bag of text with entities guessed
> after the fact.
>
> Qdrant is written with embed_triplets=True, which embeds the relationship rather
> than the two things either side of it. That's why "where did the team disagree
> with itself" retrieves anything at all — no message contains the word
> "disagree". The edge does.

**The architectural decision I'd defend hardest:**
> The classifier depends on neither the graph nor the vector store. It's pure
> Python over JSON. The graph adds multi-hop retrieval on top.
>
> That's not an outage fallback — it's an honesty mechanism. It lets me say every
> number comes from a tool and the LLM only writes the sentence around it. Which
> is a far stronger claim than "our RAG pipeline answered it".

**The routing rule:**
> The graph is for the fuzzy questions. Deterministic tools are for the exact
> ones. "Who moved first and how often did the room copy them" is a GROUP BY —
> pushing it through embeddings can only make it worse, and that's exactly where
> my one wrong retrieval came from.

## 2.8 Stories — the highest-engagement material

**S1 — the empty room. Self-deprecating, very human.**
> For the first two hours my bot was posting into a channel where the only human
> reacting was me. I'd built a system to measure group behaviour and had a group
> of one. Five people had actually joined — they just had nothing obvious to do.
> It was never a recruitment problem. It was a "you never asked them clearly"
> problem.

**S2 — the inverted answer. The best technical story.**
> Twenty minutes before the deadline, my knowledge graph told me — confidently,
> in fluent prose — that Kubernetes was the more contested repo. My data says the
> exact opposite.
>
> The cause: I'd passed a dataset UUID where the API wanted a name. It returned
> 200, said the pipeline started, and quietly created a new empty dataset named
> after my UUID. It had processed nothing and answered anyway.
>
> A pipeline that ran successfully over nothing should never look identical to one
> that ran over your data. I only caught it because I already knew the answer.

**S3 — the audit. Best "what I got wrong" material.**
> Fifteen minutes before presenting I stopped and asked: is every number on these
> slides real?
>
> Two weren't. A timestamp on slide one had been generated, not captured — it
> looked exactly like the real ones. And I was claiming "microsecond precision"
> when Slack's event_ts fraction is a sequence counter, not a clock.
>
> Both were minutes from being said out loud to judges who'd have known.

**S4 — the leak. Uncomfortable, useful, very shareable.**
> I published my knowledge graph as an HTML page. Buried in the node metadata: my
> personal email address, ten times, and an internal storage path.
>
> The export exists to be shared. That's the whole point of a visualisation. So
> the default has to be safe, and it wasn't. Caught it in a pre-publish scan,
> minutes before it would have stopped mattering that I caught it.

**S5 — the sampling trap. For a data-literate audience.**
> My corpus said 18.5% of all reactions are 👎. That's nothing like a real
> workplace, and someone would have called it out.
>
> The cause was in my own fetch script: I'd selected threads with at least ten
> reactions. That doesn't sample conversations — it samples pile-ons. The skew was
> useful, because you can't study disagreement in a sample without any. But it
> bounds every claim, and it was my own code that did it to me.

**S6 — the threshold that says "I don't know". Quietly the best product story.**
> The live dashboard shows a shape for each message. Below four reactions it
> refuses, and displays "forming — 3 more" instead.
>
> On the night, no single message ever got four. So the demo showed a system
> declining to answer, live, on a projector. I'd expected that to be the weak
> moment. It wasn't — judges trust a thing that knows its own limits more than one
> that always produces output.

**S7 — the live moment. Good if you want the human close.**
> The last slide wasn't a slide. I asked the room to open Slack and react to a
> message, then switched to a dashboard showing them arriving — names, emoji,
> seconds since the message, and the shape assembling as they clicked.
>
> That part could not be faked in advance, and nobody in the room could reproduce
> it the next day. Not because the code is secret. Because the moment is gone.

## 2.9 The research base (if you want the credibility section)

> None of this is measured from my data — it's the published work that motivated
> building it:
>
> • Miller et al., ICWSM 2016 — shown an identical rendering of an emoji, people
>   disagreed about whether it was positive or negative 25% of the time. The
>   misunderstanding survives everyone seeing the same picture.
> • Zhukova & Herring, Indiana University — Gen Z and non-binary respondents read
>   👍 and 😂 as significantly more sarcastic and passive-aggressive; older
>   respondents rated 🔥 and 💣 more negatively. Both sides think the other is
>   being rude.
> • Glikson et al., SPPS 2018 — smileys in work email lowered perceived
>   competence; held in a preregistered replication at N=847 (Lai & Mayiwar,
>   Collabra 2023).
> • Atlassian/YouGov — 65% of 10,000 workers across five countries use emoji to
>   convey tone at work.
>
> Which is why the goal is to disambiguate the emoji people already use, never to
> nudge them into using more.

## 2.10 Limitations (keep at least one — this is what makes the rest credible)

> • The shape comparison rests on 21 classified Kubernetes messages against 211
>   for vscode. Suggestive, not established.
> • The corpus is selected on high reaction counts, so it's a picture of contested
>   conversation, not a baseline of how teams normally talk.
> • Live capture that night was 17 reactions from 4 people. The capture path is
>   proven; the sample is not.
> • Slack's event_ts is second-resolution plus ordering, not sub-second precision.
> • Reactions are not outcomes. This is a triage signal, not a decision driver.

## 2.11 Ethics / positioning (recommended — pre-empts the obvious objection)

**Short:**
> Nobody is ranked. It's a translator, not a monitor.

**Full:**
> The first question anyone asks is whether this is surveillance. It isn't, and
> the design makes that checkable rather than a promise:
>
> Timing has no valence — a cascade is not "happy". I claim nothing about what
> anyone felt, only how a group behaved in time. A person is named only where
> they carry load, never as praise or blame. And capture happened in a channel
> created for it, opened with a consent notice, joined voluntarily — the main
> event channel was deliberately never ingested.
>
> That restraint turned out to be a differentiator with the judges rather than a
> limitation.

## 2.12 Closings

**C1 — the modest one (current):**
> First place at the Cognee x Qdrant Hack Night in Berlin. Thanks to both teams —
> notes on what I'd improve in the stack are in the repo.

**C2 — the question. Best for comments.**
> One thing I still don't know: does a room where everyone follows the first
> reactor have a culture problem, or just a trust surplus? I genuinely can't tell
> from the timing alone.

**C3 — the invitation:**
> It runs on your own Slack in about five minutes — Socket Mode, no public host,
> no OAuth server, one process. Everything is open.

**C4 — the reflection:**
> The thing I'll take from this: the numbers weren't the hard part. Knowing which
> ones I could actually defend was.

**C5 — the sharp one:**
> Your team already has this data. It's being deleted, continuously, right now.

## 2.13 Hashtags

Keep 3–5, plain text always. Options by audience:

> Data/eng: #DataEngineering #KnowledgeGraphs #VectorSearch #Python
> Product/leadership: #RemoteWork #TeamCulture #AsyncWork #Communication
> Event: #Hackathon #Berlin
> Vendors (plain, or @-mention in the composer instead): #cognee #Qdrant #Slack

## 2.14 Assets you can attach

> • docs/assets/four-shapes-hero.png — the four arrival patterns as a timing plot.
>   Strongest single image; it *is* the taxonomy, no text to misread.
> • The live dashboard screenshot — names arriving with per-reaction latency.
> • The dialect comparison — vscode vs kubernetes side by side.
>
> LinkedIn favours a single strong image over a carousel for link posts. The
> four-shapes plot is the one that makes someone stop scrolling.

---
---

# Notes on the choices

**The hook is the whole post.** Line 1 is all that shows before "see more", so it
must carry a concrete, falsifiable claim rather than "excited to share".

**The win goes at the end.** The insight earns the attention; placing first place
in the last line reads as confidence rather than announcement.

**Keep a limitation.** "What I got wrong" is what makes every other number
credible, and it's the section most likely to start a real conversation — which is
what actually drives reach.

**One idea per post.** This file contains material for four or five different
posts. Resist merging them: the modules that fit together are hook → problem →
method → one number → one limitation → close. Everything else is a second post.

**Never style** names, @-mentions, hashtags or URLs. A styled hashtag indexes as
nothing and a styled URL can't be copied. Add @-mentions in the composer after
pasting, so autocomplete can still match them.
