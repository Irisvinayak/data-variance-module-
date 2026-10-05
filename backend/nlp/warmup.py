# warmup.py — load the embedding model and verify the index before the first
# user needs them (code review RAG-05).
#
# The ~1.3 GB model loads lazily on the first NL request, which measured 115 s
# for that one user. Doing it in a background thread at startup moves the cost
# off the request path without delaying startup: the server accepts requests
# immediately, and an NL request that arrives early simply waits on the same
# loader lock it always did.
#
# Strictly best-effort and import-lazy: a deployment without faiss or
# sentence-transformers (manual routes only) must still boot, so every failure
# here is logged and swallowed.

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def warm_nlp() -> None:
    started = time.monotonic()
    try:
        from . import embedder, index_manifest, nlp_config

        vector = embedder.embed_query("warm up")
        try:
            dim = int(len(vector))
        except TypeError:                   # a stand-in that returns no vector
            dim = None
        problems = index_manifest.verify_index_folder(nlp_config.INDEX_DIR, nlp_config.EMBED_MODEL, dim)
        for problem in problems:
            logger.warning("[nlp.warmup] index check: %s", problem)
        logger.info(
            "[nlp.warmup] embedding model ready in %.1fs (%d index problem(s) reported)",
            time.monotonic() - started, len(problems),
        )
    except Exception as exc:  # noqa: BLE001 - never let warm-up affect startup
        logger.warning("[nlp.warmup] skipped: %s: %s", type(exc).__name__, exc)
