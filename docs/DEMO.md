# Reaction Dynamics — 90-second demo

## One-line pitch

Reaction Dynamics listens to when Slack reactions arrive, turns those timings
into team-behavior patterns, and finds work that was acknowledged but never
actually answered.

## Demo flow

### 0:00–0:15 — Open with the translation error

> “A 👍 can mean yes, seen, I’ll do it, or I’m done arguing. Slack shows the
> count. We measure what the room actually did over time.”

Show two messages with the same reaction count but different arrival shapes in
the [evidence dashboard](../dashboard.html). The point is the contrast, not the
classifier mathematics.

### 0:15–0:35 — Explain the irreplaceable signal

> “Slack’s Web API gives us names and counts, but no reaction timestamps. The
> timestamp exists only in the live event. If we do not listen now, nobody can
> reconstruct it tomorrow.”

Show the running `seed/listen_slack.py` process or react to a prepared message in
the consented demo channel.

### 0:35–0:55 — Turn the signal into a work-quality outcome

Run:

```bash
python3.12 seed/ask.py --raw "which asks did nobody answer?"
```

> “Five 👍 reactions can look like progress while the request still has no
> owner and no substantive reply. This turns that ambiguity into a follow-up.”

Then show one split or cascade. Phrase it as a prompt for a better conversation,
never as a diagnosis of what people felt.

### 0:55–1:15 — Show why Cognee and Qdrant matter

Open the [interactive graph](../graph.html) and select a past query.

> “The finding is a typed node connected to the message, room, and first mover.
> Cognee builds that graph; Qdrant embeds the relationships, so we retrieve the
> edge—not just a similar-looking message.”

Point out that all reported numbers come from one of four deterministic tools;
the LLM only routes and writes the final sentence.

### 1:15–1:30 — Land the value and guardrail

> “This improves follow-through without turning people into a leaderboard. It
> is a translator, not a monitor: observable behavior, consented channels, and
> no claims about emotion or performance.”

End on the concrete action: clarify ownership, reopen a false consensus, or make
a room’s reaction conventions explicit.

## Questions to keep ready

| Question | Short answer |
|---|---|
| Why can’t Slack analytics already do this? | Batch APIs and exports omit per-reaction timestamps and ordering. |
| Is a cascade good or bad? | Neither. It is a behavioral shape that can prompt investigation. |
| Why use GitHub data? | It is a public benchmark with per-user reaction content and timestamps. |
| Why Cognee? | Findings are typed graph nodes rather than entities guessed from a text blob. |
| Why Qdrant? | `embed_triplets=True` makes relationships searchable. |
| What happens if the LLM fails? | Keyword routing and all numeric tools still work. |
| Is this employee monitoring? | No ranking, no inferred emotion, and raw Slack capture is limited to consented rooms. |

## Reliable fallback

If Slack, the LLM, or the network is unavailable, the core demo remains local:

```bash
python3.12 seed/shapes.py --dialects --twins
python3.12 -m http.server 8000
```

Use the included GitHub corpus and open `http://localhost:8000/dashboard.html`.
