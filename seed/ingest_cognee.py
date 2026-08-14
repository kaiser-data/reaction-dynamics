"""Shapes -> typed DataPoints -> cognee -> Qdrant, with the edges embedded.

    python seed/ingest_cognee.py                       # both corpora, 40 messages
    python seed/ingest_cognee.py --limit 20 --query-only

Why this exists rather than a plain remember(): the generic document path stores
text and lets an LLM guess at entities. That produces a graph of *nouns*. What we
want in the graph is the finding -- "this room answered with a cascade and 85% of
it copied one person" -- as a first-class node with edges to the message, the
room, and the person who moved first.

`embed_triplets=True` then embeds the RELATIONSHIP, not just the nodes either
side of it. That is the honest answer to "why Qdrant": we are not searching
messages, we are searching the edges between them.

No LLM is involved. Embeddings only, so this path cannot die on a rate limit --
which matters at 20:30.
"""

import argparse
import json
import os
import sys

os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")   # no LLM here

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _guard  # noqa: E402,F401  -- MUST precede cognee; see docs/HANDOFF.md

import asyncio  # noqa: E402

import qdrant_util  # noqa: E402
from cognee.low_level import DataPoint  # noqa: E402
from cognee.tasks.storage import add_data_points  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shapes import classified, load  # noqa: E402


# ---------------------------------------------------------------- the schema

class Person(DataPoint):
    name: str
    metadata: dict = {"index_fields": ["name"]}


class Room(DataPoint):
    name: str
    platform: str
    metadata: dict = {"index_fields": ["name"]}


class Message(DataPoint):
    text: str
    author: Person
    room: Room
    metadata: dict = {"index_fields": ["text"]}


class ReactionShape(DataPoint):
    """The finding itself, as a node. `description` is a sentence on purpose --
    an embedded edge is only searchable if it reads like something a person
    would ask about."""
    description: str
    shape: str
    room: Room
    message: Message
    first_reactor: Person
    metadata: dict = {"index_fields": ["description"]}


class UnansweredAsk(DataPoint):
    """Acknowledged but never answered. The original point of the whole thing."""
    description: str
    room: Room
    message: Message
    metadata: dict = {"index_fields": ["description"]}


# ------------------------------------------------------------------ sentences

SHAPE_PROSE = {
    "cascade": ("a cascade -- most of the reactions arrived in a burst right "
                "after the first one. That is social proof, not independent "
                "agreement: the room followed whoever moved first"),
    "trickle": ("a trickle -- reactions arrived evenly and independently, which "
                "is the trustworthy kind of agreement"),
    "stall-burst": ("a stall then a burst -- the room stayed silent, then moved "
                    "all at once. That reads as deference: people waited to see "
                    "what one person thought"),
    "split": ("a split -- the room disagreed with itself, and the final "
              "reaction counts hide that completely"),
    "mixed": "no clean arrival pattern",
}


def shape_sentence(r):
    who = r.get("first_reactor", "unknown")
    s = (f"In {r['channel']}, a message by {r['user']} drew {r['n']} reactions in "
         f"{r['span_human']} and the room answered with {SHAPE_PROSE[r['shape']]}. "
         f"{who} reacted first, after {r['latency_human']}, with "
         f"{r['first_emoji']}, and {r['followed_first']:.0%} of what followed "
         f"copied {who}. Burstiness {r['burstiness']:+.2f}.")
    if r.get("split"):
        s += (f" {r['split']['for']} reacted in favour and "
              f"{r['split']['against']} against.")
    return s


# ------------------------------------------------------------------ ingestion

def build(corpus_paths, limit):
    people, rooms = {}, {}
    points, n_shapes, n_ghosts = [], 0, 0

    def person(name):
        if name not in people:
            people[name] = Person(name=name)
            points.append(people[name])
        return people[name]

    def room(name, platform):
        if name not in rooms:
            rooms[name] = Room(name=name, platform=platform)
            points.append(rooms[name])
        return rooms[name]

    for path in corpus_paths:
        if not os.path.exists(path):
            print(f"  skip {path} (not found)")
            continue
        corpus = load(path)
        platform = corpus.get("source", "unknown")

        # Ghosted asks need the raw threads, not the classified rows: a message
        # with reactions and no replies is exactly "acknowledged, never answered".
        for t in corpus["threads"]:
            msgs = t["messages"]
            root = msgs[0]
            if root.get("reactions") and len(msgs) == 1:
                rm = room(t["channel"], platform)
                m = Message(text=(root.get("text") or t["title"])[:600],
                            author=person(root["user"]), room=rm)
                points.append(m)
                points.append(UnansweredAsk(
                    description=(
                        f"In {t['channel']}, {root['user']} posted something that "
                        f"drew {len(root['reactions'])} reactions and not one "
                        f"reply. It was acknowledged but never answered."),
                    room=rm, message=m))
                n_ghosts += 1

        rows = classified(corpus)
        rows.sort(key=lambda r: -r["n"])
        for r in rows[:limit]:
            rm = room(r["channel"], platform)
            m = Message(text=(r["text"] or r["title"])[:600],
                        author=person(r["user"]), room=rm)
            points.append(m)
            points.append(ReactionShape(
                description=shape_sentence(r),
                shape=r["shape"],
                room=rm,
                message=m,
                first_reactor=person(r.get("first_reactor", "unknown")),
            ))
            n_shapes += 1

    return points, n_shapes, n_ghosts


QUERIES = [
    "which room followed whoever reacted first",
    "where did the team disagree with itself",
    "what was acknowledged but never answered",
    "which message did people react to only after a long silence",
]


async def run_queries():
    from cognee.infrastructure.databases.vector import get_vector_engine_async
    engine = await get_vector_engine_async()
    colls = [c for c in qdrant_util.collections() if c != "__error__"]
    # The triplet collection is the whole point; search it first if present.
    colls.sort(key=lambda c: (0 if "riplet" in c else 1, c))

    for q in QUERIES:
        print(f"\n  ? {q}")
        shown = 0
        for coll in colls:
            try:
                res = await engine.search(coll, query_text=q, limit=2,
                                          include_payload=True)
            except Exception:
                continue
            for r in sorted(res or [], key=lambda x: getattr(x, "score", 1.0))[:2]:
                p = getattr(r, "payload", None) or {}
                txt = p.get("description") or p.get("text") or p.get("name")
                if not txt:
                    continue
                # adapter returns 1 - cosine similarity: LOWER is better
                print(f"    [{coll[:22]:<22} dist={getattr(r,'score',9):.3f}] {str(txt)[:150]}")
                shown += 1
            if shown >= 3:
                break
        if not shown:
            print("    (nothing matched -- has the ingest run?)")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40,
                    help="shaped messages per corpus (a SLICE, not the corpus)")
    ap.add_argument("--query-only", action="store_true")
    args = ap.parse_args()

    corpora = [os.path.join(HERE, "corpus_github.json"),
               os.path.join(HERE, "corpus_slack.json")]

    print(f"embeddings : {os.getenv('EMBEDDING_PROVIDER')} / "
          f"{os.getenv('EMBEDDING_MODEL')}")
    print(f"qdrant     : {qdrant_util.describe()}")

    if not args.query_only:
        before = qdrant_util.collections()
        if "__error__" in before:
            print("FAIL -- Qdrant unreachable")
            sys.exit(1)

        points, n_shapes, n_ghosts = build(corpora, args.limit)
        print(f"\nbuilt {len(points)} data points: {n_shapes} reaction shapes, "
              f"{n_ghosts} unanswered asks")

        print("writing with embed_triplets=True (embeds the EDGES, not just nodes)...")
        await add_data_points(points, embed_triplets=True)

        after = qdrant_util.collections()
        wrote = qdrant_util.total_points(after) - qdrant_util.total_points(before)
        print(f"wrote {wrote} points into Qdrant")
        print(f"collections: {[c for c in after if c != '__error__']}")

    print(f"\n{'=' * 72}\nRELATIONSHIP QUERIES (lower distance = better match)\n{'=' * 72}")
    await run_queries()
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"\nFAIL -- {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
