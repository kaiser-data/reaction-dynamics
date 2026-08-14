# Field notes: cognee + Qdrant + Slack

Written after building **Reaction Dynamics** at the Cognee × Qdrant Hack Night,
Berlin, 2026-08-14 (first place). Everything here was hit in one evening of real
use — a three-hour build against the live APIs, not a survey.

The framing is deliberately concrete: what broke, what it cost, and what would
have saved the time. Nothing here is a complaint about a young product; the parts
that worked are called out too, because they're the parts worth protecting.

---

## 1. cognee

### 1.1 What worked, and is worth protecting

**Typed `DataPoint` subclasses are the reason this project scored.** Modelling
`ReactionShape`, `UnansweredAsk`, `Person`, `Room`, `Message` as typed nodes meant
the *finding* became a first-class object with edges to the message, the room and
the person who moved first — rather than a bag of text with entities guessed by an
LLM after the fact. That distinction is the whole pitch, and it is cognee's
genuine differentiator against "chunk it and embed it".

**`embed_triplets=True` is the single most valuable flag in the product** and it
is badly under-sold. Embedding the *relationship* rather than the two nodes either
side of it is what lets a query like *"where did the team disagree with itself"*
retrieve anything at all — no message contains the word "disagree"; the **edge**
describes it. This deserves to be in the first paragraph of the README, with
exactly that example. We found it by reading source.

### 1.2 `import cognee` dies on a *partial* Langfuse config

**Cost: ~25 minutes, and it was the first thing that happened.**

With `LANGFUSE_PUBLIC_KEY` set but `LANGFUSE_SECRET_KEY` absent (an ordinary state
for a shared `.env`), `import cognee` raises a pydantic `ValidationError` at import
time. Not at first use — at import.

We shipped a `_guard.py` that strips partial Langfuse config before the import:

```python
# [guard] dropped partial LANGFUSE config (would break cognee import)
```

**Suggested fix:** treat observability config as optional-by-construction. If the
trio is incomplete, log one warning and disable the integration. An optional
dependency should never be able to prevent the library from importing.

### 1.3 `/api/v1/cognify` takes dataset **names**, and silently creates a dataset when given an ID

**Cost: ~6 minutes, and it nearly shipped a wrong answer to judges.**

This is the most dangerous thing we hit. We passed the `dataset_id` returned by
`/api/v1/add`:

```jsonc
POST /api/v1/cognify  {"datasets": ["a094d64b-15c0-50dd-a33b-eb140480b0d2"]}
// 200 OK
// -> {"db039618-…": {"status":"PipelineRunStarted",
//                    "dataset_name":"a094d64b-15c0-50dd-a33b-eb140480b0d2",
//                    "payload":[]}}
```

It returned **200**, reported `PipelineRunStarted`, and created a **brand-new empty
dataset whose name was our UUID**. The real dataset was never processed. The only
tell was `"payload": []`, which is easy to miss in a wall of JSON.

Downstream this produced a confidently wrong answer: with no data in the graph, a
recall query asserted that *kubernetes* was the more contested room — the exact
inverse of our finding. On stage that would have been unrecoverable.

**Suggested fix, in order of preference:**

1. Accept **either** a name or a UUID — detect the UUID shape and resolve it.
2. If only names are supported, **reject** a UUID-shaped name with a 4xx rather
   than creating a dataset from it.
3. At minimum, make `"payload": []` on a cognify run a **warning in the response
   body** — "0 documents matched; did you mean dataset X?"

The general principle: a pipeline that runs successfully over nothing should never
look identical to one that ran over your data.

### 1.4 Re-ingest inflates counts — nothing dedupes

Every ingest mints fresh UUIDs, so re-running against the same corpus multiplies
points instead of converging. The practical workflow became "reset the whole store
before any re-ingest" (`./reset.sh --yes`), which is fine at hackathon scale and
untenable in production.

**Suggested fix:** a content-hash identity option on `DataPoint`, so re-ingesting
an unchanged finding is a no-op. `source_content_hash` already exists in the node
payload but appears unused (`null` on every node we inspected).

### 1.5 The Cloud graph export leaks the owner's identity

**This is the one I'd fix first.**

`GET /api/v1/visualize` returns a self-contained HTML page — genuinely nice, and we
shipped it. But every node's metadata carries:

```jsonc
"source_user": "martinkaiser.bln@googlemail.com",
"raw_data_location": "s3://cognee-prod-cluster-tenant-storage/tenant-7dfa2563-…/data/…"
```

We published that file to a public repo before noticing. It exposed the owner's
**personal email address, 10 times**, plus the **internal tenant storage path**.

The export is *designed to be shared* — that is what a visualisation is for — so
the default should be safe:

- Strip `source_user`, `raw_data_location`, and tenant paths from the export by
  default; offer `?include_provenance=true` for the internal case.
- Or ship the raw graph JSON and let the caller render, rather than embedding
  operational metadata in a shareable artifact.

Related: the exported page loads **d3 from `d3js.org` and fonts from
`fonts.googleapis.com`**. That breaks under a strict CSP, in an air-gapped review,
and on conference wifi — which is precisely when you demo it. We vendored d3 inline
(+280 KB) and stripped the font links to make it survive. **Inline the dependency
by default.**

### 1.6 Retrieval quality notes

Measured against our own 64-triplet graph, four relationship queries:

| Query | Winner | Distance | Verdict |
|---|---|---|---|
| what was acknowledged but never answered | `Triplet_text` | **0.348** | correct |
| where did the team disagree with itself | `Triplet_text` | 0.539 | correct |
| react only after a long silence | `Triplet_text` | 0.414 | correct |
| which room followed whoever reacted first | `Triplet_text` | 0.371 | **wrong document** |

Two structural observations:

- **Near-duplicate triplets are not deduped.** Query 2 returned the same source
  message twice at 0.539 and 0.542, consuming the result list. A dedupe pass on
  `source_node_id` at retrieval time would be a cheap, large win.
- **The distance band is narrow** — everything landed between 0.33 and 0.63, so
  correct and incorrect hits sat ~0.05 apart. There is no threshold that separates
  them. Any product surface that says "we found a match" needs either a
  reranker or an explicit "low confidence" state.

### 1.7 The official Slack integration cannot see reactions

We evaluated it and deliberately did not adopt it. It takes the HTTP-webhook route
and subscribes to `app_uninstalled`, `tokens_revoked`, `app_home_opened` — there is
no `reaction_added`. It therefore **structurally cannot observe the signal this
entire project is built on**, and it requires a public host, a backend, a frontend,
ngrok and OAuth to run at all.

**Suggested fix:** offer a Socket Mode path. It needs no public host, no ngrok and
no OAuth server — `pip install slack_sdk`, fill in two tokens, run one process.
That is a dramatically better "works on Monday" story, and adding
`reaction_added` / `reaction_removed` to the subscription list unlocks a class of
behavioural signal that no batch tool can reconstruct.

---

## 2. Qdrant

### 2.1 What worked

Qdrant was the least troublesome component of the entire stack — it did not fail
once. Worth stating plainly, because the notes below are all about *understanding*
it, not fixing it.

Final state: **206 points across 13 collections**, one collection per DataPoint
type, on Qdrant Cloud (`eu-central-1`).

```
Triplet_text                64   <- the relationships; the whole point
EdgeType_relationship_name  85
ReactionShape_description   12
Person_name                 12
Message_text                13
UnansweredAsk_description    1
Room_name                    2
```

### 2.2 The latency finding that surprised us

Measured, three queries, warm:

```
Qdrant Cloud search (embed + ANN, 64 triplets)   median 699 ms
```

**Almost none of that is Qdrant.** ANN over 64 vectors is sub-millisecond; the
699 ms is one embedding-API round trip plus a hop to `eu-central-1`. Worth saying
out loud because the intuition runs the other way — people assume the vector
database is the slow part and optimise the wrong layer.

For anyone tuning this: cache embeddings for repeated queries before you touch
Qdrant configuration. We measured our own tiers as:

```
exact question   -> deterministic tool   ~90 ms    no network, no LLM
fuzzy question   -> Qdrant                ~700 ms  embedding round trip dominates
narrative answer -> LLM                   ~4.7 s   optional
```

### 2.3 Sharp edges (cognee's Qdrant adapter, not Qdrant itself)

- **Distance semantics invert.** The adapter returns `1 − cosine`, so **lower is
  better**. Anything named `score` reads as "higher is better" to most people; we
  sorted the wrong way first. Name it `distance`.
- **`include_payload=False` is the default**, which returns results you cannot
  render. Almost every real caller wants the payload.
- **Payload indexes and Qdrant Cloud strict mode** conflict on `belongs_to_set` —
  we ship an `ensure_payload_indexes()` helper to work around it.
- **Silent fallback to local Qdrant.** An ad-hoc script that didn't load `.env`
  quietly wrote to a *local* instance and produced a plausible-looking collection
  set (`SlackMessage_text`, 35 points). Nothing warned us. A one-line
  "connected to: <url>" on client construction would have saved ~15 minutes of
  debugging a phantom.

---

## 3. Using the three together

### 3.1 The architecture that earned the score

```
Slack (Socket Mode) ─┐                          <- timing exists ONLY here
                     ├─> one corpus schema ─> shape classifier ─> typed DataPoints
GitHub Reactions ────┘        (JSON)          (stdlib only)            │
                                                                        v
                                                        cognee ─> Qdrant (triplets)
                                                                        │
                                                                4-tool agent
```

**The decision that mattered most: the classifier depends on neither cognee nor
Qdrant.** It is pure Python over corpus JSON. The graph adds multi-hop retrieval
*on top*. When retrieval returned a wrong document during rehearsal, the product
still worked — we routed that question to a deterministic tool and demoed the
graph on the questions it answered well.

**Recommendation to anyone building on cognee:** keep a graph-free path to your
core numbers. Not as a fallback for outages — as an *honesty mechanism*. It lets
you say "every number comes from a tool; the LLM only writes the sentence", which
is a much stronger claim than "our RAG pipeline answered it".

### 3.2 Route by question type, not by capability

The split that emerged, and that we said on stage:

> **The graph is for the fuzzy questions. The tools are for the exact ones.**

*"Who moved first and how often did the room copy them"* is a `GROUP BY`, and
sending it through embeddings can only make it worse — that is where our one wrong
retrieval came from. *"Where did the team disagree with itself"* has no keyword and
genuinely needs the edge embedding.

A cognee-side feature that would make this easy: let a `DataPoint` declare that a
field is **exact-queryable**, and expose a structured query path beside the
semantic one. Right now every question is a nearest-neighbour question.

### 3.3 Slack-specific notes for anyone doing this next

- **`reaction_added` is the only source of per-reaction ordering.** The Web API
  returns `{name, users, count}` — no timestamps, no order. This is excluded by
  construction, not an indexing gap. If your listener isn't running, that signal
  is gone permanently.
- **`event_ts` is not microseconds.** It is `seconds.<6-digit sequence>`, and the
  fraction is a Slack-side counter. You get second-resolution wall clock **plus
  strict ordering**. Ordering is what behavioural analysis actually needs — but do
  not claim sub-second precision. We nearly did.
- **Bots may post; bots must never react.** Synthetic reactions would make the
  shapes authored rather than observed and destroy the central claim.
- **Socket Mode: use `slack_sdk.socket_mode.builtin`.** The `aiohttp` variant
  needs an async program; the default client needs `websocket-client`. The builtin
  one is sync and stdlib-only.
- **Redirect stdout with `python -u`** or the listener log stays empty and you
  will think it died.
- **Never post message text through `python3 -c`** — backticks get
  command-substituted by the shell and you post a message with holes in it. We did
  this twice.

### 3.4 Consent is a feature, not paperwork

We captured in a purpose-made channel opened with a consent notice, and
deliberately **did not** ingest the main event channel. That restraint was a
differentiator with judges rather than a limitation — it made "not a monitor" a
verifiable claim instead of a slogan.

For cognee, whose product is *organisational memory*: shipping a first-class
notion of **ingestion scope with an audit trail** — this channel yes, that one no,
here is the record — would be a genuine enterprise differentiator, not a
compliance checkbox.

---

## 4. Summary — the five changes with the highest leverage

1. **Strip `source_user` and tenant paths from `/visualize` by default.** It leaked
   a personal email into a public repo. Highest severity here by a distance.
2. **Make `cognify` reject or resolve a dataset UUID** instead of silently creating
   an empty dataset and reporting success. It produced a confidently wrong answer.
3. **Never let a partial optional-integration config break `import cognee`.**
4. **Dedupe near-identical triplets at retrieval**, and expose a confidence signal —
   a 0.05 gap between right and wrong is not a usable threshold.
5. **Offer a Socket Mode Slack path with `reaction_added`.** The current webhook
   integration cannot see reactions at all, and Socket Mode needs no public host.

And the one to protect: **`embed_triplets=True` searching the edges rather than the
nodes is the best idea in the product.** Lead with it.
