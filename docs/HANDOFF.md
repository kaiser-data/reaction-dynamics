# Handoff — Cognee × Qdrant hackathon, "Give Your Slack a Memory"

**Written 2026-08-14.** Event is Friday, 18:00–21:00, Paul-Lincke-Ufer 39–40, Hof 4.
Everything below was measured on this machine, not assumed. Where something is
unverified it says so.

Two repos are in play:

| Path | Role |
|---|---|
| `cognee-slack/prep/` | All planning docs. No product code. |
| `cognee-qdrant-slack-test/` | The working stack — venv, `.env`, probes, verifiers. **This is where code runs.** |

---

## 1. Where the concept landed

It moved a long way during the session. The current version, in one line:

> **An emoji translator for teams.** The same 👍 means different things to different
> people — measurably so. We learn what each reaction *actually does* in a given
> workspace, and use that to surface requests that were acknowledged but never
> answered.

### The headline signal is reaction *timing*, not waiting time

This was a late pivot and it matters more than anything else in this document.

**Waiting time was demoted to a supporting metric.** It is a good metric and a bad
demo: you cannot watch someone wait, it needs weeks of timestamps (forcing a
synthetic corpus), and every competitor can compute it from an export.

**Reaction dynamics is the headline** because:
- It resolves in **seconds**, so it fits inside a 90-second pitch.
- The audience can participate — ask the room to react, watch the graph build.
- It is **structurally impossible to backfill**. Slack's Web API returns reactions
  as `{name, users, count}` — no timestamps, no ordering. Per-reaction timing
  exists *only* in the `reaction_added` event. No export, no batch tool, no
  competitor's corpus can contain it. It is excluded by construction, not
  overlooked.

Four arrival shapes carry the meaning — **identical reaction counts, different rooms**:

| Shape | Pattern | Reads as |
|---|---|---|
| Cascade | 8 reactions in 6s, 7 after the first | Enthusiasm, or social proof / following the first mover |
| Trickle | 5 spread over 40m | Independent agreement — the trustworthy kind |
| Stall then burst | Long silence, then 5 in 9s | Deference — the room waited to see what one person thought |
| Split | ✅/⛔ interleaved | Live disagreement, which final counts completely hide |

**Why this survives the "reactions aren't sentiment" critique** (which killed the
earlier reaction ideas): timing has no valence. A cascade is not "happy". We make
no claim about what anyone felt or approved — only about how the group behaved in
time. That is the defensible version of "understand emotions": group feeling,
never individual mind-reading.

### Three axes, and the Goodhart trap

Speed, quality and engagement are independent. **Fast is not good.**

- `Fast + token` — "ok 👍" in 40 seconds. Perfect on speed, zero on quality.
  **This is the pathology, not the goal.**
- `Slow + substantive + confirmed` — a real answer next morning. Must never rank
  below the row above.

**The trap to avoid building:** if a team is shown a response-time score and told
to improve it, the cheapest way is to reply *faster with less* — more 👍, more
"ok", fewer answers. Optimising for speed manufactures the exact disease the tool
detects. So speed is reported as context and **never as a target**; quality is the
only axis phrased as improvable.

---

## 2. Verified working — with evidence

All measured 2026-08-14 in `cognee-qdrant-slack-test/`. cognee **1.4.2**, adapter **0.4.0**.

| Leg | State | Evidence |
|---|---|---|
| Qdrant Cloud | **working** | 10 collections, eu-central-1, `QDrantAdapter` resolves |
| cognee → Qdrant writes | **working** | `t0b` wrote 11 points, no LLM involved |
| `embed_triplets=True` | **working** | `Triplet_text` populated; relationship query returns the right edge at distance 0.24 |
| Embeddings | **working** | Voyage `voyage-3-large`, 1024d |
| Full loop end to end | **working** | `remember()` **0.71s/message** on a fresh ingest. Asked cold, *"Which request did nobody pick up?"* → *"Slack export parsing"* |
| LLM structured output | **9 of 13 pass** | see table below |
| Slack bot token | **working** | `xoxb-` for `cogneegraph`, **all 7 scopes granted** (read back from the auth response header, not trusted from the config page) |
| GitHub Reactions API | **verified** | returns `user` + `content` + `created_at` per reaction |

### The ingest-speed finding

`prep/validation.md` flagged cognify timing as the highest-risk unknown, fearing
"~30 minutes for ~200 threads". **Measured at 0.71s/message**, so 200 messages is
roughly 3 minutes. The fear was wrong; you can afford a second ingest pass.

### Model sweep — pick on edge quality, not cost

At 268 tokens/message × 200 messages ≈ 54k tokens, **cost is not a real variable**
(fractions of a cent on every provider). What varies is edge quality, and since
the pitch is "we search the relationships", the edges *are* the product.

| Model | Structured | Tokens | Edge produced | Verdict |
|---|---|---|---|---|
| gemini-flash-lite | **0.9s** | 176+92 | `Tom -[APPROVED]-> Qdrant migration` | **Primary.** Fastest, leanest, best edge |
| deepseek-v4-flash | 1.7s | 513+358 | `Tom -[approved]-> Qdrant migration` | **Fallback**, keeps you on Nebius |
| qwen3-30b-a3b | 1.4s | 304+97 | `Maria -[said]-> …` | models the speech act, not the decision |
| gemma-3-27b | 2.6s | 32+153 | `Maria -[said in]-> Slack` | fewest tokens, junk edge |
| nemotron-lightning | 2.3s | 454+487 | `Maria -[said in]-> Slack` | leaks reasoning into chat |
| qwen3-32b | 2.8s | 304+93 | `Maria -[said in]-> Slack` | leaks `<think>` tags |
| gemini-flash-latest / 3-flash | 2.9s / 3.9s | 176+386 / 176+900 | `Maria -[said in]-> Slack` | slower and more verbose for a worse edge |
| **llama-3.3-70b ← in `.env`** | **39.9s** | 384+59 | `Maria -[said]-> Tom` | **worst on every axis. Remove.** |
| nemotron-nano, glm-5.1, minimax-m2.5, gpt-oss-120b | — | — | `AttributeError: 'NoneType'` | reasoning models returning no `content`; would give a **silently empty graph** |

---

## 3. Fixed during this session

Both were real blockers, both are done:

1. **`belongs_to_set` payload indexes** — cognee filters on this field but never
   creates the index. Local Qdrant tolerates it; **Qdrant Cloud runs strict mode
   and 400s mid-cognify** with `Index required but not found`. The repo already had
   `qdrant_util.ensure_payload_indexes()` — it had simply never been run against
   the cloud cluster. **All 10 collections now indexed.** Idempotent — re-run it
   whenever a new collection appears.

2. **Stale migration lock** — `.cognee-migration-*.lock` beside the SQLite state,
   left by an interrupted run, caused
   `MigrationError: (sqlite3.OperationalError) unable to open database file`.
   Delete the lock and re-run. Cost ~10 minutes to diagnose; costs 10 seconds once known.

---

## 4. Blocked — in priority order

### 🔴 `SLACK_APP_TOKEN` is empty — this is now the whole project

Socket Mode is the **only** transport for `reaction_added`, and an app-level token
is the only way to open Socket Mode. When waiting time was the headline this was a
nice-to-have. **Now the headline depends on it entirely.**

api.slack.com/apps → your app → **Basic Information → App-Level Tokens →
Generate**, scope `connections:write` → paste as `SLACK_APP_TOKEN`. Then confirm
one real event arrives before Friday.

### 🔴 Bot is not in the channel

Every read returns `not_in_channel`. `channels:join` is deliberately not in the
manifest, so the bot cannot add itself. In Slack, run:

```
/invite @cogneegraph
```

in `#<redacted-channel>` (`<REDACTED-channel-id>`).

### 🟡 `.env` still on llama-3.3-70b

A 44× speed penalty on the only slow step, and the worst edge extraction of the
nine passing models. Recommended, **not yet applied** (deliberately — it's your call):

```bash
LLM_PROVIDER=gemini
LLM_MODEL=gemini/gemini-flash-lite-latest
LLM_API_KEY=<GEMINI_API_KEY>
# fallback, one-line switch:
# LLM_PROVIDER=custom
# LLM_MODEL=nebius/deepseek-ai/DeepSeek-V4-Flash
```

### 🟡 The workspace is essentially empty

`#<redacted-channel>` is a brand-new workspace's default channel. An empty
channel produces an empty graph and there is no recovering that at 20:00. See §5.

---

## 5. Data — how to fill it

### The trap: you cannot backdate a Slack message

`chat.postMessage` posts at *now*. Seed 180 messages at 17:00 and every message is
seconds apart, so latency and ordering are degenerate — a product about timing,
demoed on data where nothing took time.

### The answer: GitHub reactions (verified this session)

The GitHub REST Reactions API returns **exactly what Slack cannot**:

```
user= kohlikohl        content= +1   created_at= 2016-07-04T09:21:52Z
user= davidpmccormick  content= +1   created_at= 2016-07-20T08:43:43Z
user= steveinatorx     content= +1   created_at= 2016-08-19T19:07:07Z
```

Per-user identity, per-reaction content, **per-reaction timestamp**, in arrival
order. Public, readable without auth. `microsoft/vscode#519` alone has 4,788 reactions.

Why it fits better than any emoji dataset available:

- **TweetEval / SemEval-2018 emoji** (45k tweets, 20 labels) — the emoji is a
  *classification label* on a standalone tweet. No threads, no reactions, no reactors.
  Wrong shape.
- **Discord dumps on HuggingFace** — stripped conversational text.
- **GH Archive** — looks right, but its `issue_comment` payload carries reactions
  as aggregate `{type, count}`: the same lossy shape as Slack. Cannot reconstruct ordering.
- **GitHub Reactions API** — threads (issue → comments), `@mentions`, real latency
  spanning weeks, ghosted issues, pinned issues, *and* timestamped per-user reactions.

And the culture fit is almost too good: **GitHub's `+1` problem is the most
documented case in existence of a reaction used as low-information
acknowledgement.** Maintainers have complained about "+1 spam" for a decade. That
*is* the "👍 means seen, not done" pathology, in public, at scale, with timestamps.

Demoing on GitHub also proves the concept is not a Slack toy — same substrate,
different platform — and lets you put **two communities side by side** where the
same emoji means different things. That is the translator's reason to exist in one slide.

**Scope:** 2–3 repos, ~40 reaction-heavy issues. Use `gh auth token` —
unauthenticated is 60 req/hour, authenticated is 5,000.

**Not yet written:** `seed/fetch_github.py`. This was offered and not started.

### Three corpora, each doing one job

| Corpus | Job |
|---|---|
| GitHub (real, timestamped) | The batch story and the dialect comparison |
| Authored Slack-export JSON | Fallback, and any Slack-specific framing. Plant findings deliberately and write `seed/PLANTED.md` so you know the answers before the stage |
| Live Slack workspace | One moment only — react on stage, watch the edge appear |

---

## 6. Files produced

All in `cognee-slack/prep/`. Open with `open <file>`.

| File | What it is |
|---|---|
| `EMOJI-HEADLINE.html` | **Read first.** The reaction-dynamics pivot, four shapes, three axes, the research with sources, the demo script |
| `THE-IDEA.html` | The specification — latency definitions, substance classifier, learned emoji taxonomy, culture metrics. **Published to Netlify** |
| `DASHBOARD.html` | The product mockup, rebuilt friendly (light, help-first, human language) |
| `RUNBOOK.html` | Eight copy-paste prompts with buttons, time boxes, gates, fallbacks. **Order is now stale — see §8** |
| `BATTLE-PLAN.html` | Run-day ops: readiness, traps table, timeline, fallbacks, checklist |
| `netlify-site/` | Deploy scaffold — edge-function basic auth, `sync.sh`, `.gitignore` |
| `ideas.md`, `validation.md`, `RUNDAY.md`, `research-prompts.md` | Earlier prep, still accurate except where noted here |

### Design split, deliberate

Internal ops docs are dark and dense. The **product** (`DASHBOARD.html`) is light
and warm — because a near-black dashboard reads as a surveillance console, which
is the opposite of what this is.

The dashboard was rebuilt after feedback that it looked intimidating. The fix was
not softer colours: it was changing what the page *is*. It opened with a score; it
now opens with **"Three people are still waiting on you"** and the actual
questions, with *"only you can see this"* underneath. A to-do list, not a report card.

Other inversions worth preserving:
- Colour encodes **duration, not virtue** — one hue, light→dark. Validated with
  the dataviz palette script, not eyeballed (first light-mode attempt failed at
  1.97:1 against a 2:1 floor and was darkened).
- A person is named **only when carrying load** — *"Maya has answered 40% — that's
  a lot to carry"* — framed as fragile for her, never as praise or blame.
- The big red "what this does not measure" panel was **deleted**. Listing the
  surveillance you are not doing makes everyone think about surveillance. One warm
  line at the bottom replaces it.

---

## 7. Published site

**https://<redacted>.netlify.app** — user `team`, password `<REDACTED-see-local-copy>`

Netlify's built-in password protection is a paid feature, so this uses an **Edge
Function doing HTTP Basic Auth** — server-side, the password never reaches the
browser, and it **fails closed** (503 if `SITE_PASSWORD` is unset, verified).

- Site ID `<REDACTED-site-id>`, team `<redacted-team>`
- `sites:create` needs `--account-slug <redacted-team>` or it errors "No teams available"
- Source of truth is `prep/THE-IDEA.html`; `netlify-site/sync.sh` copies it to
  `public/index.html` so the deployed page cannot drift
- Published page was scanned for tokens, keys, channel IDs, workspace names and
  cluster URLs — **zero hits**
- Password lives in `netlify-site/.site-password` (mode 600, gitignored)

⚠️ **Known divergence:** `netlify/edge-functions/auth.ts` was edited locally to
accept *any* username (the original required `team`, and the two-field browser
prompt was the reason sign-in appeared broken). **That edit was never deployed.**
Live still requires `team`; the local file does not check the username. Either
deploy it or revert it — it should not be left drifted.

---

## 8. Open decisions and next actions

1. **Get `SLACK_APP_TOKEN`** and confirm one `reaction_added` event arrives.
   Highest priority — the headline does not exist without it.
2. **`/invite @cogneegraph`** to the test channel.
3. **Switch the extraction model** off llama-3.3-70b (§4).
4. **Resolve the `auth.ts` divergence** (§7).
5. **Write `seed/fetch_github.py`** — pull issues + comments + per-reaction records,
   normalise into the same shape as the Slack ingest so both corpora share one code path.
6. **Reorder `RUNBOOK.html`.** Its prompt order predates the pivot and is now
   wrong: the listener is P6 at 20:10 and marked optional. It must be the **first
   thing alive at 18:00**, because the signal only accumulates while it runs.
   The waiting-time metrics slot should become the **shape classifier**
   (cascade / trickle / stall-burst / split).
7. **Decide** whether `DASHBOARD.html` and `EMOJI-HEADLINE.html` also go on Netlify
   behind the same password. Currently local only.

### Structural property worth preserving

The metrics layer has **no dependency on cognee or Qdrant** — pure Python over the
corpus JSON. So a graph failure cannot take the product down with it. If the
substrate slips, skip to metrics and come back. **The metrics are the product; the
graph is the argument for how it scales.**

---

## 9. Research — sources, verified this session

All five point the same way: emoji carry real weight at work, and their meaning is
**local, contested, and currently shifting**. That is the empirical case for
*measuring* behaviour rather than assuming meaning — i.e. for the translator.

| Finding | Source |
|---|---|
| People shown an **identical rendering** disagreed on whether its sentiment was positive/neutral/negative **25% of the time**; disagreement increases across platform renderings | [Miller et al., ICWSM 2016](https://ojs.aaai.org/index.php/ICWSM/article/view/14757) |
| First emoji sentiment lexicon: 751 emoji, ~70,000 tweets, 83 annotators, 13 European languages. Most emoji skew **positive** — which is why naive sentiment makes every workspace look cheerful | [Kralj Novak et al., PLoS ONE 2015](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0144296) |
| Non-binary and Gen Z respondents rated emoji messages as significantly more **sarcastic, passive-aggressive and threatening**. 👍 and 😂 now read negatively to many younger users; older respondents rated 🔥 and 💣 worse. **The strongest argument for the product** — the same 👍 means different things to two people in the same channel today | [Zhukova & Herring, Indiana University](https://scholarworks.iu.edu/journals/index.php/li/article/view/40800) |
| 549 participants across 29 countries: smileys in work email did not raise warmth and **lowered perceived competence**, reducing information shared in reply | [Glikson et al., SPPS 2018](https://journals.sagepub.com/doi/abs/10.1177/1948550617720269) |
| Preregistered replication, **N=847** vs the original experiment's N=85: the competence penalty **held**, the claimed formality moderation **did not**, and warmth came out positive regardless of formality | [Lai & Mayiwar, Collabra 2023](https://online.ucpress.edu/collabra/article/9/1/90195/197987/The-Dark-Versus-Bright-Side-of-a-Smiley-A) |
| 10,000 workers across US, France, Germany, India, Australia: **65% use emoji to convey tone** at work | [Atlassian/YouGov via Axios](https://www.axios.com/2022/10/23/thumbs-up-emoji-gen-z-workplace-communication) |

**Build implication of the Glikson pair:** do *not* ship a feature that nudges
people to use more emoji. Help teams **disambiguate the ones they already use**.
Citing the replication alongside the original is also the kind of care the judging
panel rewards — evaluation is Sahar Mor's most repeated public theme.

---

## 10. Traps, verified on this machine

| Symptom | Cause | Fix |
|---|---|---|
| `import cognee` fails, pydantic `ValidationError` | `LANGFUSE_PUBLIC_KEY` exported globally with no secret. Traceback names neither langfuse nor your code | **Always `import _guard` first** |
| `Selected: qdrant / lancedb` | `VECTOR_DB_PROVIDER=qdrant` alone is not enough; cognee has no auto-correct branch for a community adapter | `VECTOR_DATASET_DATABASE_HANDLER=qdrant`. The error text points at access control — wrong road |
| `400 Index required but not found for "belongs_to_set"` | Qdrant Cloud strict mode | `qdrant_util.ensure_payload_indexes()` — **already applied** |
| `MigrationError: unable to open database file` | stale `.cognee-migration-*.lock` | delete the lock, re-run |
| Best matches look worst | adapter returns `1 − cosine similarity`, a **distance** | **lower is better**; sort ascending |
| Results are bare UUIDs | `include_payload` defaults to `False` | pass `include_payload=True` |
| No `Triplet_text` collection | plain `remember()` uses the generic document path | the sponsor story needs typed `DataPoint` subclasses + `add_data_points(..., embed_triplets=True)` |
| Ingestion slows 15 min, no error | cognee auto-throttles to 60 req/60s for 900s after **any** provider rate-limit error, silently | `AUTO_RATE_LIMIT=false`; use your own key, not the organisers' |
| Point counts keep growing | every run mints fresh UUIDs; nothing dedupes | `./reset.sh --yes` — **required** after changing embedding model or dimensions |
| `timeout` command missing | not on macOS | omit it, or `gtimeout` from coreutils |
| Adapter installs cognee 0.5.6 | PyPI adapter stops at 0.2.4 and pins the old cognee | install from git (see `cognee-qdrant-slack-test/README.md`) |

---

## 11. The doctrine

> **Start the listener first — it is the only signal that cannot be recovered.
> Ingest a slice, not a corpus. Lock the concept at 18:10. Freeze at 20:15.
> Demo what works, say plainly what doesn't.**

And the tone rule, which is a feature and not a disclaimer:

> **Nobody is ranked. It is a translator, not a monitor. It exists to prevent a
> misunderstanding, not to score a person.**
