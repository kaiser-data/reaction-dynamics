"""Pull reaction-heavy GitHub issues into the shared corpus shape.

GitHub's Reactions API returns exactly what Slack cannot: per-user identity,
per-reaction emoji, and a per-reaction timestamp, in arrival order. That is the
only public source of the signal this project is about.

    python seed/fetch_github.py                          # defaults, ~40 issues
    python seed/fetch_github.py --repos vercel/next.js kubernetes/kubernetes
    python seed/fetch_github.py --per-repo 20 --min-reactions 10
    python seed/fetch_github.py --probe                  # read the cache, fetch nothing

Auth comes from `gh auth token` (5,000 req/hour). Unauthenticated is 60/hour and
will not finish. Every HTTP response is cached under seed/.cache/ keyed by URL,
so a second run costs nothing and an interrupted run resumes.

No cognee, no Qdrant, no venv requirement beyond the stdlib.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
OUT = os.path.join(HERE, "corpus_github.json")

API = "https://api.github.com"

# Two communities, deliberately unalike, because the pitch is that the same
# emoji means different things in different rooms. A vendor-run product repo and
# a foundation-run infra repo are about as far apart as public GitHub gets.
DEFAULT_REPOS = ["microsoft/vscode", "kubernetes/kubernetes"]

MENTION = re.compile(r"@([A-Za-z0-9][A-Za-z0-9-]{0,38})")


# ---------------------------------------------------------------- http + cache

def gh_token():
    tok = os.getenv("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def get(url, token, tries=4):
    """GET with on-disk caching and backoff. Returns parsed JSON, or None on 404.

    Cached by URL hash: reaction history is immutable, so a hit is always safe
    and makes re-runs free. Delete seed/.cache/ to refetch.
    """
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cognee-slack-seed",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })

    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            with open(path, "w") as f:
                json.dump(data, f)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            # 403/429 here is nearly always the secondary rate limit, which asks
            # you to slow down rather than stop. Respect Retry-After if given.
            if e.code in (403, 429) and attempt < tries - 1:
                wait = int(e.headers.get("Retry-After") or (2 ** attempt) * 5)
                print(f"    rate limited ({e.code}), sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            print(f"    HTTP {e.code} on {url}", file=sys.stderr)
            return None
        except Exception as e:
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"    {type(e).__name__} on {url}", file=sys.stderr)
            return None
    return None


# ---------------------------------------------------------------- normalising

def login(node):
    """Deleted accounts come back as null. A None in the graph is a landmine."""
    if not node:
        return "ghost"
    return node.get("login") or "ghost"


def reactions_for(url, token):
    """Per-reaction records. The aggregate `reactions` block on the parent object
    is the lossy Slack-shaped version -- we never use it except to skip zeros."""
    out = []
    for page in (1, 2, 3):  # 300 reactions is far past the point of signal
        data = get(f"{url}?per_page=100&page={page}", token)
        if not data:
            break
        for r in data:
            out.append({
                "user": login(r.get("user")),
                "emoji": r.get("content", ""),
                "ts": r.get("created_at"),
            })
        if len(data) < 100:
            break
    # Arrival order is the signal. GitHub returns ascending already; sorting is
    # cheap insurance against that changing.
    out.sort(key=lambda x: x["ts"] or "")
    return out


def fetch_issue(repo, issue, token, min_reactions):
    """One issue -> one thread, root message + comments, reactions on each."""
    num = issue["number"]
    tid = f"{repo}#{num}"
    body = (issue.get("body") or "").strip()

    root = {
        "msg_id": f"{tid}:issue",
        "thread_id": tid,
        "user": login(issue.get("user")),
        "ts": issue["created_at"],
        "text": body,
        "is_root": True,
        "parent_id": None,
        "mentions": MENTION.findall(body),
        "reactions": [],
    }
    if (issue.get("reactions") or {}).get("total_count", 0) > 0:
        root["reactions"] = reactions_for(
            f"{API}/repos/{repo}/issues/{num}/reactions", token)

    messages = [root]

    comments = get(f"{API}/repos/{repo}/issues/{num}/comments?per_page=100", token) or []
    for c in comments:
        text = (c.get("body") or "").strip()
        m = {
            "msg_id": f"{tid}:c{c['id']}",
            "thread_id": tid,
            "user": login(c.get("user")),
            "ts": c["created_at"],
            "text": text,
            "is_root": False,
            "parent_id": root["msg_id"],
            "mentions": MENTION.findall(text),
            "reactions": [],
        }
        # Only spend a request where there is something to fetch. This is what
        # keeps a 40-issue pull inside a few hundred calls instead of thousands.
        if (c.get("reactions") or {}).get("total_count", 0) > 0:
            m["reactions"] = reactions_for(
                f"{API}/repos/{repo}/issues/comments/{c['id']}/reactions", token)
        messages.append(m)

    total_reactions = sum(len(m["reactions"]) for m in messages)
    if total_reactions < min_reactions:
        return None

    return {
        "thread_id": tid,
        "source": "github",
        "channel": repo,
        "title": issue.get("title") or "",
        "url": issue.get("html_url") or "",
        "state": issue.get("state") or "",
        "messages": messages,
    }


def search_issues(repo, count, token):
    """Reaction-heavy issues first. The Search API is the only endpoint that can
    sort by reactions; it caps at 100/page and 30 req/min, so we take one page."""
    q = urllib.parse.quote(f"repo:{repo} is:issue", safe="")
    url = (f"{API}/search/issues?q={q}&sort=reactions&order=desc"
           f"&per_page={min(count, 100)}")
    data = get(url, token)
    if not data:
        return []
    return data.get("items", [])


# ---------------------------------------------------------------------- report

def summarise(corpus):
    """Doubles as the export probe: run it and you know your own numbers before
    you stand up. Never demo on a corpus whose counts you have not read."""
    threads = corpus["threads"]
    msgs = [m for t in threads for m in t["messages"]]
    reacts = [r for m in msgs for r in m["reactions"]]
    timed = [r for r in reacts if r["ts"]]

    print(f"\n{'=' * 62}\nCORPUS: {corpus['source']}\n{'=' * 62}")
    print(f"  threads            {len(threads)}")
    print(f"  messages           {len(msgs)}")
    print(f"  with >=1 reaction  {sum(1 for m in msgs if m['reactions'])}")
    print(f"  reactions          {len(reacts)}")
    print(f"  timestamped        {len(timed)}   <- the signal Slack cannot give you")
    print(f"  distinct reactors  {len({r['user'] for r in reacts})}")
    print(f"  distinct authors   {len({m['user'] for m in msgs})}")
    print(f"  threads w/ 2+ msgs {sum(1 for t in threads if len(t['messages']) > 1)}")
    print(f"  mentions           {sum(len(m['mentions']) for m in msgs)}")

    per_repo = {}
    for t in threads:
        d = per_repo.setdefault(t["channel"], {"threads": 0, "reactions": 0, "emoji": {}})
        d["threads"] += 1
        for m in t["messages"]:
            for r in m["reactions"]:
                d["reactions"] += 1
                d["emoji"][r["emoji"]] = d["emoji"].get(r["emoji"], 0) + 1

    print("\n  per repo -- the dialect comparison, in one table:")
    for repo, d in per_repo.items():
        top = sorted(d["emoji"].items(), key=lambda kv: -kv[1])[:4]
        mix = "  ".join(f"{k} {v}" for k, v in top)
        print(f"    {repo:<28} {d['threads']:>3} threads  "
              f"{d['reactions']:>5} reactions   {mix}")

    if timed:
        span = sorted(r["ts"] for r in timed)
        print(f"\n  reaction span      {span[0][:10]} -> {span[-1][:10]}")
    print()


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="+", default=DEFAULT_REPOS)
    ap.add_argument("--per-repo", type=int, default=20,
                    help="issues to consider per repo (default 20)")
    ap.add_argument("--min-reactions", type=int, default=5,
                    help="drop threads with fewer than this many timestamped reactions")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--probe", action="store_true",
                    help="summarise the existing corpus file, fetch nothing")
    args = ap.parse_args()

    if args.probe:
        if not os.path.exists(args.out):
            print(f"no corpus at {args.out} -- run without --probe first")
            sys.exit(1)
        with open(args.out) as f:
            summarise(json.load(f))
        return

    token = gh_token()
    if not token:
        print("No GitHub token. `gh auth login`, or export GITHUB_TOKEN.")
        print("Unauthenticated is 60 requests/hour and will not finish.")
        sys.exit(1)
    print(f"token ok ({len(token)} chars)")

    threads = []
    for repo in args.repos:
        print(f"\n{repo}")
        items = search_issues(repo, args.per_repo, token)
        print(f"  {len(items)} candidate issues")
        for i, issue in enumerate(items, 1):
            t = fetch_issue(repo, issue, token, args.min_reactions)
            mark = "ok " if t else "skip"
            n = len(t["messages"]) if t else 0
            r = sum(len(m["reactions"]) for m in t["messages"]) if t else 0
            print(f"  [{i:>2}/{len(items)}] {mark} #{issue['number']:<7} "
                  f"{n:>3} msgs {r:>4} reactions  {(issue.get('title') or '')[:44]}",
                  flush=True)
            if t:
                threads.append(t)

    corpus = {
        "source": "github",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repos": args.repos,
        "threads": threads,
    }
    with open(args.out, "w") as f:
        json.dump(corpus, f, indent=1)

    summarise(corpus)
    print(f"wrote {args.out}  ({os.path.getsize(args.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
