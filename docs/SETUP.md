# Setup — Slack, Qdrant, Cognee

A linear path from clone to a working graph. Roughly 20 minutes, most of it
waiting on Slack's UI.

**Read this alongside [`.env.example`](../.env.example),** which is the reference for
every variable and every trap we hit. This file is the happy path; that file is why
the happy path has the shape it does. War stories are in
[`LEARNINGS.md`](LEARNINGS.md).

---

## Step 0 — Do nothing, and see it work

Before configuring three services, confirm the thing they support actually
interests you. **The classifier needs no keys, no accounts, and no network:**

```bash
git clone https://github.com/kaiser-data/reaction-dynamics.git
cd reaction-dynamics
python3.12 seed/shapes.py --dialects --twins
```

That analyses 36,779 timestamped GitHub reactions using only the standard library.
There is also a browser version at
[`site/playground.html`](../site/playground.html) — open the file directly, no
server needed.

If you only want to *understand* the classifier, you are done. Steps 1–3 are for
capturing your own Slack and asking multi-hop questions of the graph.

---

## Step 1 — Slack (required for live capture)

Live capture is the only way to get per-reaction timing. Slack's Web API returns
`{name, users, count}` with no timestamps and no ordering, so an export cannot
substitute.

### 1a. Create the app from the manifest

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** →
   **From an app manifest**
2. Pick your workspace
3. Paste the contents of [`slack-app-manifest.json`](../slack-app-manifest.json)

The manifest sets everything for you: Socket Mode on, the eight bot scopes, and the
three bot events (`message.channels`, `reaction_added`, `reaction_removed`). Pasting
it is meaningfully less error-prone than clicking through the scope list.

### 1b. Two tokens, and they are different things

| Token | Where | Looks like | Why |
|---|---|---|---|
| **Bot token** | OAuth & Permissions → Install to Workspace | `xoxb-…` | Web API calls: `auth.test`, `users.info` |
| **App-level token** | Basic Information → App-Level Tokens → Generate, scope `connections:write` | `xapp-…` | Opens the Socket Mode WebSocket |

**Both are required.** Reaction timing is impossible with only the bot token —
there is no live event stream without the app-level token.

### 1c. Invite the bot and get the channel ID

```
/invite @cognee-graph
```

Then: right-click the channel → **View channel details** → the ID (`C0…`) is at the
bottom of the pane. Use the ID, not the name.

> **Consent matters here.** You are recording when identifiable people react. Use a
> channel whose members know, and read [Responsible use](../README.md#responsible-use)
> before pointing this at a real team channel.

### 1d. Fill in and verify

```bash
cp .env.example .env
# set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_TEST_CHANNEL

python3.12 -m pip install slack_sdk
python3.12 seed/capture.py --channel C0YOURCHANNEL
```

Expect: `connected as <bot> in <workspace>`, then a live counter. React to something
in the channel and watch the tally move.

**If a scope looks missing:** adding a scope does **not** update a live token. You
must reinstall the app. This bites everyone once.

---

## Step 2 — Qdrant

Two options. Local is faster to get green and is what `.env.example` defaults to.

### Option A — local (recommended first)

```bash
docker run -p 6333:6333 -p 6334:6334 -v "$(pwd)/qdrant_storage:/qdrant/storage" qdrant/qdrant
```

(`.env.example` mentions a `./run_qdrant.sh` helper. That script lives in the
sibling test harness this project grew out of and is **not** in this repo — the
docker one-liner above is the equivalent.)

```env
VECTOR_DB_PROVIDER=qdrant
VECTOR_DB_URL=http://localhost:6333
VECTOR_DB_KEY=
VECTOR_DATASET_DATABASE_HANDLER=qdrant
```

### Option B — Qdrant Cloud

1. [cloud.qdrant.io](https://cloud.qdrant.io) → create a free cluster
2. Copy the cluster URL and an API key

```env
VECTOR_DB_PROVIDER=qdrant
VECTOR_DB_URL=https://xyz.eu-central-1.aws.cloud.qdrant.io:6333
VECTOR_DB_KEY=your-api-key
VECTOR_DATASET_DATABASE_HANDLER=qdrant
```

Cloud costs about 320 ms per query in round-trip latency from a laptop (measured,
see the README's stack section). That is fine, and worth knowing before you go
hunting for it in your own code.

### Two Qdrant gotchas that cost us real time

**`VECTOR_DATASET_DATABASE_HANDLER=qdrant` is required and documented nowhere.**
Cognee auto-corrects this field for pgvector and Turso but has no branch for
Qdrant, because Qdrant is a community adapter. Left unset it silently stays
`lancedb` and your first write dies with a message naming
`ENABLE_BACKEND_ACCESS_CONTROL` — which sends you the wrong way. Disabling access
control also clears the error, at the cost of silently surrendering multi-user ACL.
Set the handler instead.

**`VECTOR_DB_PROVIDER=qdrant` alone does nothing.** The community adapter must also
be registered:

```python
import cognee_community_vector_adapter_qdrant.register   # side-effect import
```

`_guard.py` does this on import, which is why the seed scripts work.

---

## Step 3 — Cognee

Cognee needs an LLM for extraction and an embedder for vectors. **They are
configured separately, and that is the trap.**

### 3a. One LLM key

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini/gemini-2.5-flash
LLM_API_KEY=AIza...
```

Gemini is first-class in Cognee, fast, and cheap. `.env.example` has ready-made
blocks for OpenAI, Anthropic, Nebius, Groq, and local Ollama.

### 3b. Embeddings — set these explicitly

```env
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIMENSIONS=384
```

`fastembed` is Qdrant's own library: free, CPU-only, offline after first download,
and immune to bad conference wifi.

**Always set `EMBEDDING_DIMENSIONS`.** Cognee tries to infer it and silently falls
back to **3072** when it cannot. LiteLLM does not know Voyage's vector sizes, so
`voyage-3-large` resolves to `None` → 3072 while actually returning 1024 — a Qdrant
collection sized 3072 fed by 1024-d vectors, and no error until retrieval quality is
mysteriously terrible.

**`LLM_PROVIDER` does not imply the embedding provider.** Point the LLM at Anthropic
and change nothing else and Cognee still embeds through OpenAI — so you hit a dead
key in a step you were not watching. (Anthropic has no embeddings API at all.)

### 3c. Two settings that will save you a confused half-hour

```env
COGNEE_SKIP_CONNECTION_TEST=true   # show the provider's real error, not a 30s timeout
TELEMETRY_DISABLED=1
AUTO_RATE_LIMIT=false              # avoids 15 min of silent throttling after any 429
```

And on Langfuse: **set both keys or neither, never one.** Only the public key set
makes `import cognee` fail with a pydantic error naming neither Langfuse nor your
code.

### 3d. Ingest

```bash
python3.12 seed/ingest_cognee.py
```

This writes typed DataPoints and embeds the relationships:

```python
await add_data_points(points, embed_triplets=True)
```

That flag is the reason `"where did the team disagree with itself"` retrieves
anything — no message contains the word "disagree", but the edge does.

---

## Step 4 — Ask it something

```bash
python3.12 seed/ask.py "who moves first in this room?"
python3.12 seed/ask.py "what was acknowledged but never answered?"
```

Keyword routing picks the tool, always — the LLM never routes, it only writes the
closing sentence from the tool's output. Without an LLM key you get the same
numbers, minus the prose.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `invalid_auth` | Token not set, or app not reinstalled after a scope change |
| `channel_not_found` | Bot not invited, or the channel ID is wrong |
| Socket Mode never connects | `SLACK_APP_TOKEN` missing or lacks `connections:write` |
| `handler does not work with provider` | `VECTOR_DATASET_DATABASE_HANDLER=qdrant` not set (Step 2) |
| Retrieval quality is inexplicably bad | `EMBEDDING_DIMENSIONS` wrong; collection/vector size mismatch |
| Sorting by `score` returns the worst hits | The adapter returns `1 − cosine`. **Lower is better.** |
| `cognify` appears to hang | One Langfuse key set, or blocking span export. Unset both |
| Connection test times out after 30 s | Set `COGNEE_SKIP_CONNECTION_TEST=true` for the real error |
| Collections appear but data looks wrong | A script that did not load `.env` wrote to a *local* Qdrant |

That last one deserves emphasis: nothing warns you, and the data looks real. A
one-line `connected to: <url>` at client construction would have saved us an hour.

---

## Running it continuously

`deploy/` has a launchd agent and a systemd unit, plus the two settings whose
defaults are wrong for a capture daemon — see [`deploy/README.md`](../deploy/README.md).
Supervision restarts a dead process; the built-in watchdog handles the harder case
of a process that is alive and no longer receiving.
