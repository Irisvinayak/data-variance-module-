# embedder.py — text -> query vector, at runtime. Embeddings themselves are
# now built by an external tool and dropped into backend/output/ (see
# nlp_config.INDEX_DIR) — this module only ever embeds the user's live NL
# query so it can be compared against those pre-built vectors. The
# SentenceTransformer model is loaded lazily so importing this module doesn't
# pay the ~1.3GB model load cost until an NL query actually needs it.

from __future__ import annotations

import logging
import time

import numpy as np

from .nlp_config import EMBED_MODEL, QUERY_PREFIX

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("[nlp.embedder] Loading embedding model %s ...", EMBED_MODEL)
        # Logged with its elapsed time because this load is the single
        # slowest thing in the NL path: on a machine without the model
        # cached it downloads ~1.3GB, which has measured at 60s+ and can
        # blow a reverse proxy's request timeout even though the backend
        # itself eventually succeeds. One line per process (the model is
        # cached in _model afterwards), so it costs nothing in volume but
        # tells you immediately whether a slow first query was the model
        # or something downstream.
        started = time.monotonic()
        try:
            _model = SentenceTransformer(EMBED_MODEL)
        except Exception as exc:
            logger.error(
                "[nlp.embedder] Embedding model %s FAILED to load after %.1fs | %s: %s "
                "| needs sentence-transformers installed and either a warm "
                "HuggingFace cache or network access to fetch the model",
                EMBED_MODEL, time.monotonic() - started, type(exc).__name__, exc,
            )
            raise
        logger.info(
            "[nlp.embedder] Embedding model %s loaded in %.1fs",
            EMBED_MODEL, time.monotonic() - started,
        )
    return _model


def embed_query(query: str) -> np.ndarray:
    prefixed = QUERY_PREFIX + query
    return _get_model().encode([prefixed], normalize_embeddings=True)[0]
