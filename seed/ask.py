"""A small tool-calling agent over the reaction graph.

    python seed/ask.py "which asks did nobody answer?"
    python seed/ask.py "who moves first in vscode?"
    python seed/ask.py --tools                 # list the tools, run nothing

Four tools. The LLM only chooses which to call and writes the final sentence --
every number it reports comes from a tool, never from the model. If the LLM is
unavailable (dead key, rate limit, no network at 20:45) it falls back to keyword
routing and still answers, just without the prose. A demo that degrades to
"correct but terse" beats one that degrades to a traceback.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shapes import classified, load  # noqa: E402


def _corpora():
    out = []
    for name in ("corpus_github.json", "corpus_slack.json"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            out.append(load(p))
    return out


# ------------------------------------------------------------------- the tools

def ghosted_asks(room: str = "", limit: int = 5):
    """Messages that drew reactions and got no reply at all."""
    hits = []
    for c in _corpora():
        for t in c["threads"]:
            if room and room.lower() not in t["channel"].lower():
                continue
            root = t["messages"][0]
            if root.get("reactions") and len(t["messages"]) == 1:
                hits.append({
                    "room": t["channel"],
                    "author": root["user"],
                    "reactions": len(root["reactions"]),
                    "text": (root.get("text") or t["title"])[:140],
                    "url": t.get("url", ""),
                })
    hits.sort(key=lambda h: -h["reactions"])
    return {"count": len(hits), "top": hits[:limit]}


def bellwether(room: str = "", limit: int = 5):
    """Who reacts first most often, and how often the room copies them."""
    from collections import defaultdict
    firsts, follow = defaultdict(int), defaultdict(list)
    for c in _corpora():
        for r in classified(c):
            if room and room.lower() not in r["channel"].lower():
                continue
            who = r.get("first_reactor", "unknown")
            firsts[who] += 1
            follow[who].append(r["followed_first"])
    rank = sorted(firsts.items(), key=lambda kv: -kv[1])[:limit]
    return {"people": [
        {"person": p, "moved_first": n,
         "copied_pct": round(100 * sum(follow[p]) / len(follow[p]))}
        for p, n in rank]}


def room_dialect(room: str = "", limit: int = 6):
    """How each room uses emoji, and which arrival shapes it produces."""
    from collections import defaultdict
    per = defaultdict(lambda: {"n": 0, "shapes": defaultdict(int), "emoji": defaultdict(int)})
    for c in _corpora():
        for r in classified(c):
            if room and room.lower() not in r["channel"].lower():
                continue
            d = per[r["channel"]]
            d["n"] += 1
            d["shapes"][r["shape"]] += 1
            for k, v in r["emoji"].items():
                d["emoji"][k] += v
    out = []
    for chan, d in per.items():
        tot = sum(d["emoji"].values()) or 1
        out.append({
            "room": chan, "messages": d["n"],
            "emoji": {k: f"{100 * v / tot:.0f}%" for k, v in
                      sorted(d["emoji"].items(), key=lambda kv: -kv[1])[:4]},
            "shapes": {k: f"{100 * v / d['n']:.0f}%" for k, v in
                       sorted(d["shapes"].items(), key=lambda kv: -kv[1])},
        })
    return {"rooms": out[:limit]}


def shapes_like(shape: str = "split", limit: int = 5):
    """Messages whose reactions arrived in a given pattern."""
    hits = []
    for c in _corpora():
        for r in classified(c):
            if r["shape"] == shape:
                hits.append({
                    "room": r["channel"], "author": r["user"],
                    "reactions": r["n"], "span": r["span_human"],
                    "first_reactor": r.get("first_reactor"),
                    "copied_pct": round(100 * r["followed_first"]),
                    "text": r["text"][:120], "url": r["url"],
                })
    hits.sort(key=lambda h: -h["reactions"])
    return {"shape": shape, "count": len(hits), "top": hits[:limit]}


TOOLS = {
    "ghosted_asks": (ghosted_asks,
                     "Messages that got reactions but zero replies -- "
                     "acknowledged but never answered. Args: room, limit"),
    "bellwether": (bellwether,
                   "Who reacts first most often and how often others copy them. "
                   "Args: room, limit"),
    "room_dialect": (room_dialect,
                     "How a room uses emoji and which arrival shapes it makes. "
                     "Args: room, limit"),
    "shapes_like": (shapes_like,
                    "Messages with a given arrival shape: cascade, trickle, "
                    "stall-burst, split. Args: shape, limit"),
}


# ------------------------------------------------------------------- routing

def keyword_route(q):
    """No LLM required. Deliberately dumb and completely reliable."""
    s = q.lower()
    # Negation + "answer/reply" in any order. The first version listed exact
    # phrases ("never answered") and missed "which asks did nobody answer",
    # which is how people actually ask it.
    negated = any(w in s for w in ("nobody", "no one", "noone", "never", "didn't",
                                   "did not", "no ", "without"))
    answerish = any(w in s for w in ("answer", "reply", "respond", "pick up",
                                     "picked up"))
    if (negated and answerish) or any(w in s for w in (
            "ghost", "unanswered", "unanswer", "ignored", "acknowledg", "dropped")):
        return "ghosted_asks", {}
    if any(w in s for w in ("first", "bellwether", "moves first", "leader",
                            "follow", "social proof")):
        return "bellwether", {}
    if any(w in s for w in ("disagree", "split", "contested", "argument")):
        return "shapes_like", {"shape": "split"}
    if any(w in s for w in ("cascade", "burst", "fast")):
        return "shapes_like", {"shape": "cascade"}
    if any(w in s for w in ("trickle", "independent", "even")):
        return "shapes_like", {"shape": "trickle"}
    return "room_dialect", {}


def llm_answer(question, tool_name, result):
    """Prose only. Every number in the answer came from the tool above."""
    try:
        import litellm
        r = litellm.completion(
            model=os.getenv("LLM_MODEL", "gemini/gemini-flash-lite-latest"),
            api_key=os.getenv("LLM_API_KEY"),
            timeout=20,
            messages=[{
                "role": "user",
                "content": (
                    "Answer the question in at most three sentences using ONLY "
                    "the JSON below. Quote the exact numbers. Never claim to know "
                    "what anyone felt -- this data describes when people reacted, "
                    "not what they meant.\n\n"
                    f"Question: {question}\nTool: {tool_name}\n"
                    f"Result: {json.dumps(result)[:3000]}")
            }],
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"(no prose layer: {type(e).__name__}) -- raw tool output above."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--tools", action="store_true", help="list tools and exit")
    ap.add_argument("--raw", action="store_true", help="skip the LLM prose layer")
    args = ap.parse_args()

    if args.tools:
        for name, (_, doc) in TOOLS.items():
            print(f"  {name:<14} {doc}")
        return

    q = " ".join(args.question) or "what did this room do?"
    env_path = os.path.join(os.path.dirname(HERE), ".env")
    if os.path.exists(env_path):
        with open(env_path) as env_file:
            for line in env_file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    name, kwargs = keyword_route(q)
    fn = TOOLS[name][0]
    result = fn(**kwargs)

    print(f"\n  Q: {q}")
    print(f"  -> tool: {name}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})\n")
    print(json.dumps(result, indent=2)[:1800])

    if not args.raw:
        print(f"\n  {'-' * 66}")
        print(f"  {llm_answer(q, name, result)}")
    print()


if __name__ == "__main__":
    main()
