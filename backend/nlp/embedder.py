# embedder.py — text -> query vector, at runtime. Embeddings themselves are
# now built by an external tool and dropped into backend/output/ (see
# nlp_config.INDEX_DIR) — this module only ever embeds the user's live NL
# query so it can be compared against those pre-built vectors. The
# SentenceTransformer model is loaded lazily so importing this module doesn't
# pay the ~1.3GB model load cost until an NL query actually needs it.

from __future__ import annotations

import logging

import numpy as np

from .nlp_config import EMBED_MODEL, QUERY_PREFIX

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("[nlp.embedder] Loading embedding model %s ...", EMBED_MODEL)
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def embed_query(query: str) -> np.ndarray:
    prefixed = QUERY_PREFIX + query
    return _get_model().encode([prefixed], normalize_embeddings=True)[0]
