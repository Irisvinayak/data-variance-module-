# nlp_config.py — Config for the embedding / NL-query layer.
# Additive only: reuses backend.config's existing DB settings; never overrides them.
# All values are overridable via env vars (loaded through backend.config's load_dotenv call).
#
# Embeddings are built by an EXTERNAL tool (not by this project) and dropped
# into INDEX_DIR below as a self-contained folder: table_index.faiss/
# table_meta.pkl, column_index.faiss/column_meta.pkl, row_label_index.faiss/
# row_label_meta.pkl, schema.json, description_samples.json. This project
# only ever reads them — see backend/nlp/embedder.py (query embedding) and
# backend/nlp/index_store.py (FAISS search). Whatever tool builds them only
# needs to know table/column names — return_id/auth resolution happens live,
# in backend/nlp/return_lookup.py, against this app's own Returns.xml.

from __future__ import annotations

import os

# ── Embedding model ────────────────────────────────────────────────────────────
# MUST match whatever model built the embeddings in INDEX_DIR — a mismatched
# model produces a different vector space and similarity scores become
# meaningless. Confirm against whatever the external build tool used.
EMBED_MODEL: str = os.getenv("DV_NLP_EMBED_MODEL", "BAAI/bge-large-en")
QUERY_PREFIX: str = os.getenv(
    "DV_NLP_QUERY_PREFIX",
    "Represent this sentence for searching relevant passages: ",
)

# ── Vector index location ──────────────────────────────────────────────────────
# backend/output/ — one level above nlp/, where the embedding folder is
# actually dropped in. Override with DV_NLP_INDEX_DIR if it lives elsewhere.
NLP_DIR: str = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR: str = os.path.dirname(NLP_DIR)
INDEX_DIR: str = os.getenv("DV_NLP_INDEX_DIR", os.path.join(BACKEND_DIR, "output"))

SCHEMA_JSON_PATH: str = os.path.join(INDEX_DIR, "schema.json")
DESCRIPTION_SAMPLES_PATH: str = os.path.join(INDEX_DIR, "description_samples.json")
TABLE_INDEX_PATH: str = os.path.join(INDEX_DIR, "table_index.faiss")
TABLE_META_PATH: str = os.path.join(INDEX_DIR, "table_meta.pkl")
COLUMN_INDEX_PATH: str = os.path.join(INDEX_DIR, "column_index.faiss")
COLUMN_META_PATH: str = os.path.join(INDEX_DIR, "column_meta.pkl")
ROW_LABEL_INDEX_PATH: str = os.path.join(INDEX_DIR, "row_label_index.faiss")
ROW_LABEL_META_PATH: str = os.path.join(INDEX_DIR, "row_label_meta.pkl")

# ── Retrieval tuning ───────────────────────────────────────────────────────────
# RRF_K controls how fast a hit's influence decays with its rank in the fused
# ranking: contribution = 1/(RRF_K + rank + 1).
#
# This was hard-coded to 60 — the value from the original TREC RRF paper, tuned
# for fusing very long (thousands of docs) result lists. At that scale k=60 is
# right; at OUR scale it is actively harmful, because it makes the top-5 hits
# almost indistinguishable: 1/61 vs 1/65 is a 6.6% spread across the entire
# shortlist. Rank position effectively stopped carrying information, so a table
# that appeared at mediocre rank in several signals out-scored a table with one
# precise, rank-0 match — the exact inversion the per-signal "best hit only"
# rule further down was already trying to prevent.
#
# Measured on a 60-query benchmark built from the index's own column
# descriptions (query = a column's human description, correct = the top table
# actually contains that column): k=60 -> 55% top-1, k=20 -> 60%, k=10 -> 65%,
# k=5 -> 77%, k=2 -> 78%. Top-3 peaks at 98% at k=5. Chose 5: it captures
# essentially all of the available gain while still smoothing rank noise, where
# k<=2 approaches "trust rank 0 absolutely".
RRF_K: int = int(os.getenv("DV_NLP_RRF_K", "5"))

TOP_K_TABLES: int = int(os.getenv("DV_NLP_TOP_K_TABLES", "5"))
TOP_K_COLUMNS: int = int(os.getenv("DV_NLP_TOP_K_COLUMNS", "12"))
TOP_K_LABELS: int = int(os.getenv("DV_NLP_TOP_K_LABELS", "10"))
MIN_TABLE_SCORE: float = float(os.getenv("DV_NLP_MIN_TABLE_SCORE", "0.25"))
MIN_COLUMN_SCORE: float = float(os.getenv("DV_NLP_MIN_COLUMN_SCORE", "0.20"))

# ── Lexical (BM25) + QA-pairs signals ──────────────────────────────────────────
# Additive to the dense FAISS signals above — built by the same external tool,
# dropped into INDEX_DIR alongside the FAISS files. Both degrade to a silent
# no-op if their file is missing (see backend/nlp/lexical_search.py), so
# leaving these files out of INDEX_DIR reproduces today's exact behavior.
BM25_INDEX_PATH: str = os.path.join(INDEX_DIR, "bm25_table_index.pkl")
BM25_SIGNAL_WEIGHT: float = float(os.getenv("DV_NLP_BM25_SIGNAL_WEIGHT", "1.5"))
BM25_TOP_K: int = int(os.getenv("DV_NLP_BM25_TOP_K", str(TOP_K_TABLES * 3)))

QA_PAIRS_PATH: str = os.path.join(INDEX_DIR, "qa_pairs.json")
QA_STRONG_MATCH_THRESHOLD: float = float(os.getenv("DV_NLP_QA_STRONG_MATCH_THRESHOLD", "0.95"))
QA_STRONG_MATCH_BONUS: float = float(os.getenv("DV_NLP_QA_STRONG_MATCH_BONUS", "10.0"))

# Optional embedding pre-filter ahead of the QA strong-match difflib scan —
# at 145 pairs a full difflib scan is instant, but difflib's per-comparison
# cost (string-similarity, not a cheap vector/term lookup) makes it the
# first signal to slow down as QA pairs scale into the hundreds/thousands.
# If qa_index.faiss/qa_meta.pkl are present (same external tool, dropped in
# alongside qa_pairs.json), narrow to the QA_PREFILTER_TOP_N nearest by
# cosine similarity first and only difflib-score those — degrades to the
# original full-corpus scan if these files are absent, same as every other
# signal here.
QA_INDEX_PATH: str = os.path.join(INDEX_DIR, "qa_index.faiss")
QA_META_PATH: str = os.path.join(INDEX_DIR, "qa_meta.pkl")
QA_PREFILTER_TOP_N: int = int(os.getenv("DV_NLP_QA_PREFILTER_TOP_N", "20"))

# ── Table-resolution confidence / ambiguity (backend/nlp/confidence.py) ────────
# table_confidence >= AUTO_PROCEED  -> resolve automatically, exactly as today.
# ASK_FLOOR <= table_confidence < AUTO_PROCEED (or a tie is detected even above
# AUTO_PROCEED) -> ask the user a clarifying question instead of guessing.
# table_confidence < ASK_FLOOR -> today's existing "no match" 404, unchanged.
# Measured on the 60-query benchmark (see RRF_K): AUTO precision is FLAT at
# ~80% for every threshold from 0.40 to 0.72, then buys only 3 more points
# (83%) at 0.80 while coverage collapses from 83% to 48%. 0.60 is the knee —
# the highest value that still keeps full coverage of the plateau. Raising it
# back toward 0.72 does not buy accuracy, it only converts answers into
# questions.
CONFIDENCE_AUTO_PROCEED: float = float(os.getenv("DV_NLP_CONFIDENCE_AUTO_PROCEED", "0.60"))
CONFIDENCE_ASK_FLOOR: float = float(os.getenv("DV_NLP_CONFIDENCE_ASK_FLOOR", "0.35"))
TIE_EPSILON: float = float(os.getenv("DV_NLP_TIE_EPSILON", "0.08"))

# Relative margin over the runner-up at which the winner is considered fully
# separated: margin = (top1 - top2) / top1, and MARGIN_FULL_SEPARATION is where
# that term saturates. Fused RRF margins are small in absolute terms even for a
# clear winner (measured mean 0.18 on correct answers), so without this scaling
# the margin term would contribute almost nothing to confidence.
MARGIN_FULL_SEPARATION: float = float(os.getenv("DV_NLP_MARGIN_FULL_SEPARATION", "0.25"))

# Stage-2 return-scoped ranking (backend/nlp/scoped_retriever.py). When false,
# main.py's _shortlist_for_return falls back to _unranked_shortlist — the
# pre-ranking behaviour, constants and all — so the feature can be switched off
# without a deploy if it ever misbehaves in production.
SCOPED_RETRIEVAL_ENABLED: bool = (
    os.getenv("DV_NLP_SCOPED_RETRIEVAL", "true").strip().lower() == "true"
)

# ── Ollama (LLM-assisted intent resolution + SQL generation) ──────────────────
# Reuses the same self-hosted Ollama endpoint sql_agent already talks to.
OLLAMA_URL: str = os.getenv(
    "DV_NLP_OLLAMA_URL", "http://3.109.51.228/OllamaProxy/api/generate"
)

# Two SEPARATE model settings, deliberately — these are different tasks with
# different model requirements:
#   - intent_resolver.py asks the model to follow instructions and emit a
#     small structured JSON object -> needs a general instruction-following
#     chat model (qwen2.5, llama3.1). A SQL-completion specialist like
#     sqlcoder is trained to continue SQL text, not to obey "respond with
#     JSON only" -> it reliably fails this task (confirmed: two straight
#     ungrounded/empty responses, ~50s wasted, before falling through to a
#     404 in production logs).
#   - sql_generator.py asks the model to WRITE SQL text -> this is exactly
#     what a SQL-completion specialist is fine-tuned for.
# Setting DV_NLP_OLLAMA_MODEL now only affects sql_generator.py; intent
# resolution always uses DV_NLP_INTENT_MODEL regardless of that setting.
OLLAMA_MODEL: str = os.getenv("DV_NLP_OLLAMA_MODEL", "hf.co/defog/sqlcoder-7b-2:Q5_K_M")
INTENT_MODEL: str = os.getenv("DV_NLP_INTENT_MODEL", "qwen2.5:7b")
OLLAMA_TIMEOUT_SEC: int = int(os.getenv("DV_NLP_OLLAMA_TIMEOUT_SEC", "60"))

# intent_resolver's expected output is a ~100-char JSON object — capping
# num_predict bounds worst-case latency and stops the model rambling past
# the closing brace instead of stopping cleanly.
INTENT_NUM_PREDICT: int = int(os.getenv("DV_NLP_INTENT_NUM_PREDICT", "200"))

# Intent resolution gets its OWN (much tighter) timeout, separate from
# OLLAMA_TIMEOUT_SEC which sql_generator.py still uses. Rationale: intent
# resolution emits ~100 chars of JSON (INTENT_NUM_PREDICT above), so a
# healthy call returns in a couple of seconds — a 60s ceiling only ever
# gets hit when the endpoint is unreachable/overloaded, and since
# resolve_intent() now has an instant deterministic fallback (see
# intent_resolver._resolve_deterministic), waiting a full minute to
# discover that buys nothing but a very slow request.
INTENT_TIMEOUT_SEC: int = int(os.getenv("DV_NLP_INTENT_TIMEOUT_SEC", "20"))

# Circuit breaker: after a TRANSPORT failure (timeout/connection refused —
# not a merely-badly-formatted answer), skip the LLM entirely for this many
# seconds and go straight to the deterministic resolver. Without this, an
# Ollama outage costs every single request the full INTENT_TIMEOUT_SEC
# before falling back, turning a working-but-degraded endpoint into a
# uniformly slow one.
INTENT_FAILURE_COOLDOWN_SEC: float = float(
    os.getenv("DV_NLP_INTENT_FAILURE_COOLDOWN_SEC", "60")
)

# Cap on candidate columns listed per table in the intent prompt.
# retriever.py's backfill deliberately injects a table's COMPLETE column
# list when none of its own columns made the top-k search, so a handful of
# wide tables can balloon the prompt into thousands of tokens — which is
# paid for twice over in generation latency. Columns arrive similarity-
# ranked, so the tail is the least likely to be the answer anyway.
# Validation still accepts any column in the full shortlist, so this only
# narrows what the model is shown, never what it's allowed to pick.
INTENT_MAX_COLS_PER_TABLE: int = int(os.getenv("DV_NLP_INTENT_MAX_COLS_PER_TABLE", "40"))

# Per-model prompt/behavior profile — ported from sql_agent/src/config.py's
# MODEL_PROFILES so switching DV_NLP_OLLAMA_MODEL picks the right prompt style.
MODEL_PROFILES: dict[str, dict] = {
    "qwen2.5:7b": {
        "prompt_style": "rules", "dialect_hint": "Oracle",
        "supports_full_ruleset": True, "temperature": 0.0, "num_predict": 512,
    },
    "llama3.1:latest": {
        "prompt_style": "minimal", "dialect_hint": "Oracle",
        "supports_full_ruleset": False, "temperature": 0.0, "num_predict": 128,
    },
    "hf.co/defog/sqlcoder-7b-2:Q5_K_M": {
        "prompt_style": "minimal", "dialect_hint": "Oracle",
        "supports_full_ruleset": False, "temperature": 0.0, "num_predict": 128,
    },
}
