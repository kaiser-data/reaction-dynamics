"""Import this BEFORE cognee, always:  `import _guard`

Does three things, each of which is a documented footgun:

1. Loads .env.
2. Neutralises the LANGFUSE traps that break or silently stall cognee.
3. Registers the Qdrant vector adapter.

(3) is the one people forget. Setting VECTOR_DB_PROVIDER=qdrant on its own does
nothing -- cognee raises `unsupported vector provider` unless the adapter has
been registered before the first cognee call. Doing it here means no script can
forget it.

Verified on this machine 2026-08-14 against cognee 1.4.2 +
cognee-community-vector-adapter-qdrant 0.4.0.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# --- LANGFUSE trap A: half-config kills `import cognee` outright ---------------
# cognee's BaseConfig validator raises
#   ValidationError: Both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be
#   provided together
# if a public key is present without its secret. This machine exports
# LANGFUSE_PUBLIC_KEY + LANGFUSE_BASE_URL globally, so this fires for real --
# confirmed 2026-08-14. The traceback names pydantic, never your code.
if os.environ.get("LANGFUSE_PUBLIC_KEY") and not os.environ.get("LANGFUSE_SECRET_KEY"):
    for _v in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_BASE_URL", "TRACE_TO_LANGFUSE"):
        os.environ.pop(_v, None)
    print("[guard] dropped partial LANGFUSE config (would break cognee import)")

# --- LANGFUSE trap B: both keys set makes cognify crawl with no error ----------
# cognee then exports every span through SimpleSpanProcessor: one blocking HTTPS
# POST per span, on the event loop. Looks exactly like "cognify hung".
# COGNEE_TRACING_ENABLED=false does NOT stop it -- a model_validator re-enables
# tracing whenever a langfuse key is present. Unsetting is the only off switch.
if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
    for _v in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        os.environ.pop(_v, None)
    print("[guard] dropped LANGFUSE keys -- cognee would export spans synchronously")

# --- cognee phones home by default; not on conference wifi --------------------
os.environ.setdefault("TELEMETRY_DISABLED", "1")

# --- cognee reads LLM_API_KEY, not OPENAI_API_KEY -----------------------------
if not os.environ.get("LLM_API_KEY") and os.environ.get("OPENAI_API_KEY"):
    os.environ["LLM_API_KEY"] = os.environ["OPENAI_API_KEY"]
    print("[guard] mapped OPENAI_API_KEY -> LLM_API_KEY")

# --- Qdrant adapter registration ----------------------------------------------
# The API differs by adapter version:
#   git 0.3.0 / 0.4.0 -> `import ....register`   (side effect at import)
#   PyPI 0.2.x        -> `from ... import register; register()`  (callable)
# On 0.4.0 the old form raises `TypeError: 'module' object is not callable`
# because `register` resolves to the submodule. Try the new form first.
QDRANT_ADAPTER = None
try:
    import cognee_community_vector_adapter_qdrant.register  # noqa: F401

    QDRANT_ADAPTER = "side-effect import (0.3.x/0.4.x)"
except ImportError:
    try:
        from cognee_community_vector_adapter_qdrant import register

        register()
        QDRANT_ADAPTER = "callable register() (0.2.x)"
    except ImportError:
        QDRANT_ADAPTER = None
        print("[guard] WARNING: qdrant adapter NOT installed -- cognee will use its default store")
