"""Talk to Qdrant directly, independent of whatever cognee believes.

Kept separate because every test needs the same thing: an honest, out-of-band
read of what is actually in the store. Checking through cognee would happily
confirm cognee's own mistaken idea of where it wrote.

Handles both deployments. Local needs no auth; Qdrant Cloud rejects an
unauthenticated request with 403, so VECTOR_DB_KEY must go in an `api-key`
header. Forgetting that produces "Qdrant unreachable" against a cluster that is
perfectly healthy.
"""

import json
import os
import urllib.error
import urllib.request


def _headers():
    key = (os.getenv("VECTOR_DB_KEY") or "").strip()
    return {"api-key": key} if key else {}


def base_url():
    return (os.getenv("VECTOR_DB_URL") or "http://localhost:6333").rstrip("/")


def is_cloud():
    return "cloud.qdrant.io" in base_url()


def _get(path, timeout=15):
    req = urllib.request.Request(base_url() + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def collections():
    """{name: points_count}, or {"__error__": ...} — never raises."""
    try:
        names = [c["name"] for c in _get("/collections")["result"]["collections"]]
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code == 403:
            hint = " (403 -- set VECTOR_DB_KEY; Qdrant Cloud rejects anonymous reads)"
        return {"__error__": f"HTTP {e.code}{hint}"}
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}

    out = {}
    for n in names:
        try:
            out[n] = _get(f"/collections/{n}")["result"].get("points_count", 0)
        except Exception:
            out[n] = "?"
    return out


def total_points(state):
    return sum(v for k, v in state.items() if k != "__error__" and isinstance(v, int))


def describe():
    return f"{base_url()}  [{'cloud' if is_cloud() else 'local'}]"


def ensure_payload_indexes(fields=("belongs_to_set",)):
    """Create the keyword payload indexes cognee's adapter filters on.

    cognee filters by `belongs_to_set` but never creates an index for it. Local
    Qdrant tolerates an unindexed filter; Qdrant Cloud runs with strict mode and
    rejects it:

        400 Bad Request: Index required but not found for "belongs_to_set"
        of one of the following types: [keyword]

    which surfaces mid-cognify as an opaque failure. Creating the index is
    idempotent, so this is safe to run before every session.
    """
    made, failed = [], []
    for name in collections():
        if name == "__error__":
            continue
        for field in fields:
            body = json.dumps({"field_name": field, "field_schema": "keyword"}).encode()
            req = urllib.request.Request(
                f"{base_url()}/collections/{name}/index?wait=true",
                data=body, method="PUT",
                headers={**_headers(), "Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(req, timeout=30)
                made.append(f"{name}.{field}")
            except Exception as e:
                failed.append(f"{name}.{field}: {e}")
    return made, failed
