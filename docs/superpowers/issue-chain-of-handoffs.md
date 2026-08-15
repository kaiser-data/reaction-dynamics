# Add a third execution mode: a chain of handoffs

**Repo:** https://github.com/obra/superpowers
**Affects:** `skills/writing-plans/SKILL.md` (Execution Handoff), `skills/executing-plans/SKILL.md`

## Summary

`writing-plans` offers Subagent-Driven and Inline execution. Both carry a cost
that scales badly with plan length, in different ways, and neither is a good fit
for a long sequential plan on a usage-limited plan.

I'd like a third mode: **execute as a chain of handoffs.** One agent, units of
work executed in sequence, context reset at every unit boundary, and a written
handoff as the only thing that crosses. Each unit hands off to the next. The
handoff is not a side artifact maintained in case something goes wrong — it is
what the run is made of.

The invariant it enforces:

> **After a unit boundary, no fact needed by a later unit may exist only in
> volatile context.**

## Why both existing modes scale badly

Every turn re-sends the context it has accumulated. So the cost of a run is not
the work it does — it is the integral of the window over the turns.

| Mode | Context per unit | Tokens processed | Why |
|---|---|---|---|
| Inline | plan + *all* prior work | **O(N²)** | the window only ever grows |
| Subagent-driven | dispatch prompt + cold re-derivation | **O(N·C)** | bounded per unit, but the cold-start tax C is paid N times |
| Chain of handoffs | plan + handoff + this unit | **O(N·c)** | the handoff is pre-digested context, c ≪ C |

**Inline** is quadratic. By task 6 of an 8-task plan the window holds five tasks
of exploration, most of it dead, and every subsequent turn pays for all of it —
on every turn, not just at the end. Prompt caching lowers the constant; it does
not change the shape. Two things follow: cost grows superlinearly in plan
length, and reasoning quality falls as the ratio of live to dead material drops.

**Subagent-driven** fixes the growth but replaces it with a fixed tax per unit.
Each subagent starts cold and rebuilds the stable context — repo layout,
conventions, environment — that the session already established, then discards
it. Where units fan out in parallel that tax is amortized. Where they are
sequential it is simply paid N times for context that never changed.

**A chain of handoffs** keeps the bounded per-unit context and makes the cold
start cheap. This is the actual claim: *a handoff is a cheaper cold start than
re-derivation.* It is context pre-digested by the agent that did the work,
rather than reconstructed by one that didn't — and it carries what re-derivation
cannot recover at any price.

## The second failure mode: losing the run

On a usage-limited subscription, exhausting the budget mid-plan does not merely
cost more. The in-flight context is lost, so everything spent producing state
that lived only there is spent for nothing — the tokens are gone and so is the
work they bought.

A chain of handoffs bounds that loss structurally. The window resets to a known
floor at every boundary instead of growing until it fails, which makes
consumption predictable, and the worst case of an interruption is redoing one
unit rather than reconstructing a run.

This is the sharpest instance of the cost problem rather than a separate one.
The same accumulation degrades attention, latency and reliability on any billing
model; the subscription case is where it becomes unrecoverable instead of merely
expensive.

## What crosses the boundary

Since the reset makes re-derivation unnecessary only if the handoff is good, the
handoff's contents are not a matter of taste. Six categories exist at the end of
a unit, are absent from both the plan and the repo, and are destroyed by a
reset. They are domain-independent; the examples come from one 8-task TDD plan
(SQLite store, watchdog daemon, 41 tests) executed inline.

**1. Environment ground truth** — facts obtained by running things. *The venv is
uv-managed and has no pip, so the plan's `pip install` fails. A server from an
earlier session still holds the port a verification step uses.* Cheap to record,
expensive to rediscover, invisible in the repo.

**2. Corrections to the plan's beliefs.** *A predicted row count of 21 was
actually 23, for a legitimate reason. One test assertion in the plan was wrong
and the code was right.* This category decides the argument: a fresh context
reading only the plan sees a failing assertion and "fixes" working code. Losing
it does not cause rework, it causes damage.

**3. Negative results** — approaches tried, rejected, and why. Nothing in common
practice records these, and a fresh context cannot reconstruct them at any cost,
because a dead end leaves no trace in the repo. This is the largest systematic
waste in subagent-driven execution: every fresh subagent is free to repeat its
predecessor's mistake.

**4. Deferred obligations** — noticed, out of scope, must not be silently
dropped. *A live run showed the watchdog's `beat()` wired to a listener that
never fires when idle: the tests proved the timer logic while nothing proved the
wiring. The fix belongs to a later unit, so it survives only if the handoff
carries it.*

**5. Verification state** — what was actually run and observed, versus what was
assumed. This is what separates "done" from "believed done."

**6. Provenance** — which claims are verified and which are inferred. A transfer
format that cannot express uncertainty forces the receiver to invent confidence.
The handoff must mark its own gaps for the same reason a monitoring system
records when it wasn't watching: an unmarked absence reads as a positive result.

Categories 3 and 4 are the ones no reset mechanism can recover on its own. They
are why the chain has to be written rather than inferred.

## The loop

Stated as an invariant rather than a button, so it holds on any harness:

```
for each unit of work:
  1. load: plan + handoff (and nothing else carried across)
  2. execute
  3. verify; capture the observed output, not the expected output
  4. append to the handoff (the six categories)
  5. reset working context to {plan, handoff}
```

Step 5 has many valid implementations — an explicit clear, a new session, a
subagent whose only input is the handoff, a summarizer constrained to the
handoff. They differ in cost, not in kind, and the chain is unaffected by which
one a harness offers. That also means the same document works if the next link
is a different model, a scheduled run next week, or a person on Monday: the
artifact keeps its value under every execution mode, including the two that
already exist, which makes adopting it low-regret.

**Precondition, stated honestly:** this works when the work decomposes into units
with externally verifiable completion and durable output — files, commits,
migrations, records. If the product of the work lives only in the conversation,
there is nothing to reset *to*, and this mode does not apply.

## Why this is not auto-compaction

The most likely objection, since most harnesses now summarize under pressure.
Auto-compaction fires on *token pressure* at an arbitrary point, frequently
mid-thought; it is lossy in an uncontrolled way, because the model chooses what
to keep under duress; it is invisible, so it cannot be inspected, corrected or
versioned; and it lives in the session, so it does not survive a crash or reach
a different agent.

A handoff is boundary-aligned, deliberate, inspectable, diffable, and on disk.
The two are complementary, but compaction is a pressure-release valve and this
is a structural commitment. Relying on the valve is how you discover the window
was holding something you needed.

## When not to use it, and what can go wrong

A chain of handoffs is wrong for exploratory debugging where the state is a hunch
rather than an artifact; for plans short enough that accumulation never bites;
for work whose product *is* the conversation; and for tightly coupled units where
the boundary is artificial and resetting mid-thought costs more than the debris.

Subagents also retain one benefit this does not replicate: **failure isolation.**
A subagent that goes off the rails does not poison the parent thread. That is a
real reason to keep subagent-driven execution for risky or speculative units, and
an argument for choosing per unit rather than per plan.

The characteristic failure is **handoff drift**: the document goes stale or wrong,
and the next link trusts a bad record with fresh confidence and no raw history to
fall back on. That is worse than inline, where the original material is at least
still present. The mitigations are that entries be written from observed output
rather than intent, and that category 6 be mandatory.

The trade is an unbounded, invisible cost — accumulation — for a bounded, visible
one: a single write per boundary.

## How to tell whether the claim is true

The premise that a handoff is a cheaper cold start than re-derivation is a
hypothesis, not a finding, and it is testable in any harness from transcripts:

- **Re-derivation rate** — how often unit N+1 runs a command unit N already ran.
  Should fall sharply against subagent-driven.
- **Boundary loss** — how often unit N+1 repeats, contradicts, or reverses a
  decision made in unit N. Compare all three modes on the same plan.
- **Tokens per completed unit** — should be roughly flat across the chain, where
  inline rises with unit index. This is the quadratic-versus-linear claim, and
  it is the easiest of the three to measure.

I have not run these; a single plan is an anecdote and I would rather say so than
generalize from one branch.

## Concrete asks

1. Add the third mode to the `writing-plans` Execution Handoff and a
   corresponding section in `executing-plans`, stated as the invariant plus the
   loop so it holds with or without subagent support.
2. Specify the handoff schema — under a reset, anything unwritten is genuinely
   gone, so its contents cannot be left to taste.
3. Qualify the unconditional subagent recommendation in `executing-plans`:
   subagents where units fan out in parallel or where failure isolation is a
   correctness requirement; a chain of handoffs where units are sequential and
   the dominant cost is re-deriving stable context per unit.

## Context

Encountered while executing an 8-task TDD plan inline on a usage-limited
subscription. Inline was the right call for context reuse and the wrong one for
cost; subagent-driven would have inverted both. The discipline that made the run
survivable — resetting at unit boundaries and maintaining the handoff as I went —
is in neither skill, and I had to invent it partway through, after the window had
already grown past the point where it should have been reset.
