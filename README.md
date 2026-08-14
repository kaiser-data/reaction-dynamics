# Reaction Dynamics — an emoji translator for teams

**Cognee × Qdrant Hack Night, Berlin, 2026-08-14.**

The same 👍 means different things to different people — measurably so. This
learns what each reaction *actually does* in a given workspace, and uses that to
surface requests that were acknowledged but never answered.

The claim is deliberately narrow: **we say nothing about what anyone felt.**
Timing has no valence — a cascade is not "happy". We describe only how a group
behaved in time. That is the defensible version of "understanding emotions":
group behaviour, never individual mind-reading.

---

## The signal nobody else has

Slack's Web API returns reactions as `{name, users, count}`. No timestamps, no
ordering. Per-reaction timing exists **only** in the live `reaction_added`
event — so no export, no batch tool, and no competitor's corpus can contain it.
It is excluded by construction, not overlooked.

Four arrival shapes carry the meaning. **Identical reaction counts, different rooms:**

| Shape | Pattern | Reads as |
|---|---|---|
| **Cascade** | 8 reactions in 6s, 7 after the first | enthusiasm, or social proof |
| **Trickle** | 5 spread over 40m | independent agreement — the trustworthy kind |
| **Stall then burst** | long silence, then 5 in 9s | deference — the room waited for one person |
| **Split** | ✅/⛔ interleaved | live disagreement, which final counts hide completely |

Classification is a hypothesis test, not a hand-picked threshold: arrival times
are tested against uniform with a one-sample Kolmogorov–Smirnov statistic
(critical value `1.36/√n`), so "trickle" means *we cannot reject independent
arrival*. Burstiness uses Goh & Barabási (2008), `B = (σ−μ)/(σ+μ)`.

---

## What we measured

**36,779 timestamped reactions** across 24 threads, 16,725 distinct reactors,
2016 → 2026, pulled from the GitHub Reactions API — the only public source that
carries per-reaction identity, content *and* timestamp.

### The same emoji does different work in two rooms

| | microsoft/vscode | kubernetes/kubernetes |
|---|---|---|
| 👍 `+1` | 52% | 69% |
| 👎 `-1` | **27%** | **6%** |
| `split` shapes | **12%** | **0%** |
| `trickle` | 65% | 90% |

vscode is a contested product-feature room where a quarter of all reactions are
dissent and one message in eight is an active disagreement. kubernetes is a
consensus room — dissent is 6%, and there is no split shape at all.

**Honesty note:** the emoji-mix difference rests on 11,654 k8s reactions and is
robust. The *shape*-mix difference rests on 21 classified k8s messages against
211 for vscode — suggestive, not established. Lead with the first.

### The seconds-scale cascade is not on GitHub

Median cascade span in the corpus is **23.4 hours**. We went looking for the
six-second burst in the largest public corpus of timestamped reactions in
existence and it is not there. That shape exists only where a room is live, and
only if you were listening at the moment it happened.

Which is why the listener is the first thing that starts and the last thing
anyone can reconstruct after the fact.

---

## Run it

Needs Python 3.12 and a Slack app (manifest included).

```bash
cp .env.example .env          # fill in 6 values; every one is documented inline
pip install slack_sdk

# 1. the live signal -- start this FIRST and leave it running
python seed/listen_slack.py

# 2. the batch corpus -- no Slack needed, uses `gh auth token`
python seed/fetch_github.py

# 3. classify either corpus with the same code
python seed/shapes.py --dialects --twins
python seed/live_to_corpus.py && python seed/shapes.py --corpus seed/corpus_slack.json --window 0.5
```

**Slack app:** api.slack.com/apps → Create New App → *From an app manifest* →
paste `slack-app-manifest.json` → Install → generate an App-Level Token with
`connections:write` → `/invite @cognee-graph` in the channel.

Socket Mode is used deliberately: **no public host, no ngrok, no OAuth server,
no frontend.** Clone, fill in `.env`, run one process. The official cognee Slack
integration takes the HTTP-webhook route, which needs all four — and subscribes
to `app_uninstalled`/`tokens_revoked`/`app_home_opened`, so it cannot see
reactions at all.

---

## Layout

```
seed/schema.py           the one corpus shape; GitHub and Slack both normalise into it
seed/fetch_github.py     GitHub Reactions API -> corpus (cached, resumable)
seed/listen_slack.py     Socket Mode listener; the only source of reaction timing
seed/live_to_corpus.py   live_events.jsonl -> the same shape
seed/shapes.py           the classifier: KS test, burstiness, dialects, twins
slack-app-manifest.json  8 scopes, 3 bot events, Socket Mode on
docs/HANDOFF.md          full engineering log: traps, model sweep, verified state
```

Live Slack and ten years of GitHub go through **one schema and one classifier**,
so "cascade" means the same thing in both. The only difference is timescale —
which is the finding, not a bug to normalise away.

---

## Status — what is real

| Piece | State |
|---|---|
| GitHub corpus, 36,779 timestamped reactions | ✅ working, reproducible |
| Shape classifier + dialect comparison | ✅ working |
| Socket Mode listener | ✅ written — see docs/HANDOFF.md for run state |
| cognee → Qdrant ingest | ✅ verified separately: 0.71s/message, `embed_triplets=True`, relationship query returns the right edge at distance 0.24 |
| Shapes flowing through cognee/Qdrant as typed DataPoints | ⏳ in progress |

The metrics layer has **no dependency on cognee or Qdrant** — pure Python over
corpus JSON — so a graph failure cannot take the product down with it.

---

## Not a monitor

Nobody is ranked. Colour encodes duration, never virtue. A person is named only
when carrying load — *"Maya has answered 40% of everything — that's a lot to
carry"* — framed as fragile for her, never as praise or blame.

It exists to prevent a misunderstanding, not to score a person.

## Prior art we checked

Emoji meaning is local, contested, and currently shifting — which is the
empirical case for *measuring* behaviour rather than assuming meaning.

- Miller et al., ICWSM 2016 — people shown an **identical** rendering disagreed on its sentiment **25%** of the time
- Zhukova & Herring — 👍 and 😂 now read as sarcastic or passive-aggressive to many younger users
- Glikson et al., SPPS 2018 — smileys in work email **lowered perceived competence**; replicated at N=847 (Lai & Mayiwar, Collabra 2023), where the competence penalty held and the claimed formality moderation did not
- Atlassian/YouGov — **65%** of 10,000 workers use emoji to convey tone at work

Build implication: do **not** ship a feature that nudges people to use more
emoji. Help teams disambiguate the ones they already use.
