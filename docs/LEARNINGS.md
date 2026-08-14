# Field notes: cognee + Qdrant + Slack

Built **Reaction Dynamics** at the Cognee × Qdrant Hack Night, Berlin,
2026-08-14 (first place). These are the things that cost us time, written up so
they cost someone else less.

**Repro context.** `cognee 1.4.2` · `cognee-community-vector-adapter-qdrant 0.4.0`
· Qdrant Cloud `eu-central-1` · Cognee Cloud tenant API · macOS 15, Python 3.12.
Everything below was observed on that stack on 2026-08-14. Where a claim is an
inference rather than a measurement, it says so.

The parts that worked are called out first, because they're the parts worth
protecting.

---

## 0. What worked, and should be defended

**Typed `DataPoint` subclasses are the reason this project won.** Modelling
`ReactionShape`, `UnansweredAsk`, `Person`, `Room`, `Message` as typed nodes made
the *finding* a first-class object with edges to the message, the room, and the
person who reacted first — instead of a bag of text with entities guessed by an
LLM afterwards. That is cognee's real differentiator against "chunk it and embed
it", and it is what judges responded to.

**`embed_triplets=True` is the best idea in the product and it is buried.**
Embedding the *relationship* rather than the two nodes either side of it is what
lets *"where did the team disagree with itself"* retrieve anything at all — no
message contains the word "disagree"; the **edge** describes it. We found this
flag by reading source. It belongs in the first paragraph of the README, with
exactly that example.

---

## 1. Integration traps — all verified, all cost real time

We ended up shipping a `_guard.py` that every entry point imports before cognee.
Its existence is the finding: five separate footguns, none of which produced an
error message that pointed at the cause.

### 1.1 A partial Langfuse config breaks `import cognee`

With `LANGFUSE_PUBLIC_KEY` set and `LANGFUSE_SECRET_KEY` absent — an ordinary
state for a shared shell profile — `import cognee` raises:

```
ValidationError: Both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY
                must be provided together
```

at **import time**, not at first use. The traceback names pydantic and never your
code, so it reads as a broken install.

**Fix:** an optional observability integration should never be able to prevent the
library from importing. Incomplete config → one warning, integration disabled.

### 1.2 A *complete* Langfuse config silently makes `cognify` crawl — and the documented off-switch does not work

This is the one we'd fix first among the config traps, because it produces **no
error at all**.

With both keys set, cognee exports every span through `SimpleSpanProcessor`: one
**blocking HTTPS POST per span, on the event loop**. The symptom is indistinguishable
from "cognify hung". There is nothing in the logs.

Worse: **`COGNEE_TRACING_ENABLED=false` does not stop it.** A `model_validator`
re-enables tracing whenever a Langfuse key is present. Unsetting the environment
variables is the only working off-switch — which is not discoverable.

**Fix:** use `BatchSpanProcessor` (or an async exporter) so tracing can never block
the pipeline; and make `COGNEE_TRACING_ENABLED=false` authoritative over key
presence. A flag that a validator silently overrides is worse than no flag.

### 1.3 `VECTOR_DB_PROVIDER=qdrant` alone does nothing

Setting the provider raises `unsupported vector provider` unless the community
adapter has been **registered before the first cognee call**. Nothing in the error
suggests that a registration import is missing.

And the registration API changed shape between versions:

```python
import cognee_community_vector_adapter_qdrant.register   # 0.3.x / 0.4.x — side effect
from cognee_community_vector_adapter_qdrant import register; register()   # 0.2.x — callable
```

On 0.4.0 the old form fails with `TypeError: 'module' object is not callable`,
because `register` resolves to the submodule. We ship a try/except across both.

**Fix:** have cognee attempt the adapter import itself when the provider is set,
and raise an error that names the missing package. Keep one registration form, or
keep the callable working as a shim.

### 1.4 cognee reads `LLM_API_KEY`, not `OPENAI_API_KEY`

Almost every developer already has `OPENAI_API_KEY` exported. We map it in the
guard. **Fix:** fall back to the provider-conventional variable, or say so in the
error.

### 1.5 Telemetry is on by default

`TELEMETRY_DISABLED=1` needs to be set explicitly. On conference wifi and in
regulated environments this matters more than it sounds.

---

## 2. `POST /api/v1/cognify` accepts a dataset **UUID** and silently creates an empty dataset from it

**The highest-severity correctness issue we hit.** It nearly put a wrong answer on
stage.

`/api/v1/add` returns a `dataset_id`. Passing that id to `/cognify`:

```jsonc
POST /api/v1/cognify  {"datasets": ["a094d64b-15c0-50dd-a33b-eb140480b0d2"]}
// 200 OK
// -> {"db039618-…": {"status":"PipelineRunStarted",
//                    "dataset_name":"a094d64b-15c0-50dd-a33b-eb140480b0d2",
//                    "payload":[]}}
```

It returned **200**, reported `PipelineRunStarted`, and created a **new, empty
dataset whose name was our UUID**. The real dataset was never processed. The only
signal was `"payload": []`.

Downstream, the graph then answered a question about our data with total
confidence and got it exactly backwards — asserting that *kubernetes* was the more
contested room when our data says the opposite. We caught it with 20 minutes left
because we happened to know the right answer.

**Fix, in order of preference:**

1. Accept either a name or a UUID, and resolve the UUID.
2. If only names are supported, **reject** a UUID-shaped name with a 4xx instead of
   creating a dataset from it.
3. At minimum, treat `payload: []` as a warning in the response body —
   *"0 documents matched; did you mean dataset X?"*

**General principle:** a pipeline that ran successfully over nothing must never
look identical to one that ran over your data.

Related, smaller: `GET /api/v1/datasets/status?datasets=<id>` returns the status of
**every** dataset in the tenant, not the one asked for. We had to grep our id out
of the response.

---

## 3. The Cloud graph export leaks the owner's identity

`GET /api/v1/visualize` returns a self-contained HTML graph page. It is genuinely
good and we shipped it. But every node's metadata carries:

```jsonc
"source_user": "<owner's personal email address>",
"raw_data_location": "s3://cognee-prod-cluster-tenant-storage/tenant-<uuid>/data/…"
```

We published that file to a public repository before noticing — it exposed a
personal email address **10 times** plus the internal tenant storage path. We
caught it in a pre-publish scan; a less paranoid workflow would not have.

**The export exists to be shared.** That is what a visualisation is for, so the
default must be safe:

- Strip `source_user`, `raw_data_location` and tenant paths by default; add
  `?include_provenance=true` for the internal case.
- Or return graph JSON and let the caller render, so operational metadata never
  ends up inside a shareable artifact.

**Also:** the exported page loads **d3 from `d3js.org`** and **fonts from
`fonts.googleapis.com`**. That breaks under a strict CSP, in air-gapped review, and
on conference wifi — precisely when you demo it. We vendored d3 inline (+280 KB)
and stripped the font links. **Inline the dependency by default**; a page described
as self-contained should be self-contained.

---

## 4. Retrieval: one real finding, and a caveat about our own evidence

**Caveat first:** our index was **64 triplets** from a deliberately small slice.
Retrieval behaviour on an index that size is indicative, not conclusive. We report
it because the *mechanism* generalises, not because the sample is strong.

### 4.1 Triplet text is dominated by the source node's description

This is the useful one. With `embed_triplets=True`, one triplet is created per
edge, and its text is `<source description> -› <target>`. When the source
description is long, several triplets sharing a source are **near-identical for the
first ~400 characters** and differ only in a short tail.

Two hits from one query:

```
dist 0.5387   len 446   "In microsoft/vscode, a message by 1l0 drew 23 reactions…"
dist 0.5418   len 429   "In microsoft/vscode, a message by 1l0 drew 23 reactions…"
```

Different triplets — different edges — but a reader sees the same result twice, and
the embeddings are governed by the shared prefix rather than by the relationship
that distinguishes them. (We initially misdiagnosed this as a missing dedupe; it
isn't, and that matters, because the fix is different.)

**Fix options:**

- Put the **relationship and target first** in the triplet text, so the
  discriminating content leads and carries embedding weight.
- Or embed a summarised source rather than the full description.
- Or collapse by source node at retrieval and return the distinct edges as
  sub-results, so the caller sees one fact with several relationships instead of
  what looks like duplicate rows.

### 4.2 Distance is not comparable across queries, so it cannot be a confidence signal

Four relationship queries, `Triplet_text`, lower = better:

| Query | Top-hit distance | Correct? |
|---|---|---|
| what was acknowledged but never answered | **0.348** | yes |
| which room followed whoever reacted first | 0.371 | **no** |
| react only after a long silence | 0.414 | yes |
| where did the team disagree with itself | 0.539 | yes |

A **wrong** top hit (0.371) scored better than two **correct** ones (0.414, 0.539).
So absolute distance carries no cross-query meaning — any UI that renders it as
confidence, or any threshold applied globally, will mislead. If a confidence signal
is wanted it needs a reranker or a calibration step, not the raw distance.

---

## 5. Qdrant

Qdrant did not fail once all evening. The notes below are about *understanding* the
integration, not fixing the database.

Final state: **206 points across 13 collections**, one per DataPoint type.

```
Triplet_text                64   <- the relationships; the whole point
EdgeType_relationship_name  85
Message_text                13
Person_name                 12
ReactionShape_description   12
Room_name                    2
UnansweredAsk_description    1
```

### 5.1 Where the ~700 ms actually goes — measured, not assumed

We first assumed the embedding call dominated. It doesn't. Measured separately,
median of 3, warm:

```
embed_text only    286.8 ms
Qdrant ANN only    318.3 ms
end-to-end search  699    ms
```

Roughly an even split, and **neither number is compute** — ANN over 64 vectors is
microseconds of work. Both legs are network round trips (embedding provider, then
`eu-central-1`). The lesson for anyone tuning this: locality beats configuration.
Caching embeddings removes ~290 ms; co-locating or running Qdrant locally removes
~320 ms. Tuning HNSW parameters at this scale removes nothing.

### 5.2 Sharp edges in cognee's Qdrant adapter

- **Distance semantics invert.** The adapter returns `1 − cosine`, so **lower is
  better** — but the field is called `score`, which everyone reads as
  higher-is-better. We sorted the wrong way first. Rename it `distance`.
- **`include_payload=False` is the default**, which returns hits you cannot render.
  Nearly every real caller wants the payload.
- **Payload indexes vs Qdrant Cloud strict mode** conflict on `belongs_to_set`; we
  ship an `ensure_payload_indexes()` helper to work around it.
- **Silent fallback to a local instance.** A script that didn't load `.env` quietly
  wrote to a *local* Qdrant and produced a plausible-looking collection set. Nothing
  warned us, and the data looked real. A one-line `connected to: <url>` at client
  construction would have saved the debugging.

---

## 6. Slack

### 6.1 The official cognee Slack integration cannot observe reactions

We evaluated it and deliberately did not adopt it. It takes the HTTP-webhook route
and subscribes to `app_uninstalled`, `tokens_revoked`, `app_home_opened` — there is
no `reaction_added`. It therefore **structurally cannot see the signal this entire
project is built on**, and it requires a public host, a backend, a frontend, ngrok
and OAuth to run at all.

**Fix:** offer a **Socket Mode** path. No public host, no ngrok, no OAuth server —
`pip install slack_sdk`, two tokens, one process. That is a dramatically better
"works on Monday" story, and adding `reaction_added` / `reaction_removed` unlocks a
class of behavioural signal no batch tool can reconstruct.

### 6.2 Notes for anyone capturing reactions

- **`reaction_added` is the only source of per-reaction ordering.** The Web API
  returns `{name, users, count}` — no timestamps, no order. This is excluded by
  construction, not an indexing gap. If the listener isn't running, the signal is
  gone permanently.
- **`event_ts` is not microseconds.** It is `seconds.<6-digit fraction>`, and across
  our capture consecutive events differed by a fixed step — it orders events, it
  does not measure sub-second time. You get second-resolution wall clock **plus
  strict ordering**. Ordering is what behavioural analysis needs; do not claim
  sub-second precision. We nearly did, in a submission.
- **Use `slack_sdk.socket_mode.builtin`.** The `aiohttp` variant needs an async
  program; the default client needs `websocket-client`. The builtin one is sync and
  stdlib-only.
- **Run the listener with `python -u`**, or the redirected log stays empty and you
  will think it died.
- **Bots may post; bots must never react.** Synthetic reactions would make the
  shapes authored rather than observed.

---

## 7. Architecture note: keep a graph-free path to your core numbers

```
Slack (Socket Mode) ─┐                       <- ordering exists ONLY here
                     ├─> one schema ─> classifier ─> typed DataPoints
GitHub Reactions ────┘     (JSON)     (stdlib only)        │
                                                            v
                                              cognee ─> Qdrant (triplets)
                                                            │
                                                     4-tool agent
```

The classifier depends on **neither cognee nor Qdrant** — pure Python over corpus
JSON. The graph adds multi-hop retrieval on top. When retrieval returned a wrong
document during rehearsal, the product still worked: we routed that question to a
deterministic tool and demoed the graph on the questions it answered well.

**Recommendation to anyone building on cognee:** keep a graph-free path to your core
numbers — not as an outage fallback, but as an *honesty mechanism*. It lets you say
"every number comes from a tool; the LLM only writes the sentence", which is a much
stronger claim than "our RAG pipeline answered it".

**The routing rule that emerged:** the graph is for the fuzzy questions, tools are
for the exact ones. *"Who moved first and how often did the room copy them"* is a
`GROUP BY`; sending it through embeddings can only make it worse, and that is
exactly where our one wrong retrieval came from.

A cognee feature that would make this easy: let a `DataPoint` declare a field as
**exact-queryable** and expose a structured query path beside the semantic one.
Today every question is a nearest-neighbour question.

---

## 8. Consent as a product surface

We captured in a purpose-made channel opened with a consent notice, and
deliberately did **not** ingest the main event channel. That restraint read as a
strength to judges rather than a limitation — it made "not a monitor" verifiable
instead of a slogan.

For a product whose pitch is *organisational memory*, a first-class notion of
**ingestion scope with an audit trail** — this source yes, that one no, here is the
record — would be a genuine enterprise differentiator rather than a compliance
checkbox.

---

## 9. Top five, by leverage

1. **Strip `source_user` and tenant paths from `/visualize` by default.** It put a
   personal email into a public repo. Highest severity here.
2. **Make Langfuse tracing non-blocking, and make `COGNEE_TRACING_ENABLED=false`
   authoritative.** Today a complete config silently collapses cognify throughput
   with no error and no working off-switch.
3. **Make `cognify` resolve or reject a dataset UUID** instead of creating an empty
   dataset and reporting success. It produced a confidently wrong answer.
4. **Never let a partial optional-integration config break `import cognee`.**
5. **Lead triplet text with the relationship**, so retrieval discriminates on the
   edge instead of on a shared source-description prefix.

And the one to protect: **`embed_triplets=True` — searching the edges rather than
the nodes — is the best idea in the product. Lead with it.**
