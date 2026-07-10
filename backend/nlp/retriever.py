# retriever.py — Runtime retrieval: NL query -> authorized table/column shortlist.
# Consumes the vector store (embedder/index_store) and auth_service (existing,
# untouched department-access function) but is not part of either — this is
# the seam between "vectors" and "what this specific user is allowed to see".
#
# The embedding index itself is built by an EXTERNAL tool and just dropped
# into backend/output/ (nlp_config.INDEX_DIR) — its records only carry
# {"text", "table"} / {"text", "table", "column"}, no return_id. So this
# module never trusts return_id from the embedding metadata; it always
# resolves table_name -> return_id live via return_lookup.py (this app's own
# Returns.xml + table-mapping XML), which is also what auth/compute_variance
# already trust. That keeps the embedding index swappable/rebuildable by any
# tool without ever touching authorization.

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..auth_service import get_allowed_form_ids
from ..config import AUTH_ENABLED
from . import return_lookup
from .embedder import embed_query
from .index_store import search
from .nlp_config import (
    COLUMN_INDEX_PATH,
    COLUMN_META_PATH,
    MIN_COLUMN_SCORE,
    MIN_TABLE_SCORE,
    ROW_LABEL_INDEX_PATH,
    ROW_LABEL_META_PATH,
    TABLE_INDEX_PATH,
    TABLE_META_PATH,
    TOP_K_COLUMNS,
    TOP_K_LABELS,
    TOP_K_TABLES,
)

logger = logging.getLogger(__name__)


def _rrf(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank + 1)


def get_relevant_schema(query: str, login_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Embed `query`, search the table/column FAISS indices, RRF-fuse them into
    a ranked table shortlist, then filter that shortlist down to only tables
    belonging to return_ids `login_id`'s department is allowed to access
    (reusing auth_service.get_allowed_form_ids — no auth logic duplicated here).

    When AUTH_ENABLED=false (dev bypass, same flag auth_deps.py already
    honors), the authorization filter is skipped entirely — every retrieved
    table is treated as authorized, same as require_login/require_return_access
    do in that mode.

    Returns {"tables": [...], "columns": [...], "matched_labels": [...]} — all
    three possibly empty. matched_labels feeds sql_generator.build_prompt()'s
    "STORAGE FORMAT: VERTICAL" row-label rules.
    """
    q_vec = embed_query(query)

    table_hits = search(TABLE_INDEX_PATH, TABLE_META_PATH, q_vec, TOP_K_TABLES * 3, min_score=MIN_TABLE_SCORE)
    col_hits = search(COLUMN_INDEX_PATH, COLUMN_META_PATH, q_vec, TOP_K_COLUMNS * 4, min_score=MIN_COLUMN_SCORE)
    label_hits = search(ROW_LABEL_INDEX_PATH, ROW_LABEL_META_PATH, q_vec, TOP_K_LABELS * 3)

    all_table_meta: Dict[str, Dict[str, Any]] = {h["table"]: h for _, h in table_hits}
    scores: Dict[str, float] = {tbl: 0.0 for tbl in all_table_meta}

    for rank, (_, t) in enumerate(table_hits):
        scores[t["table"]] = scores.get(t["table"], 0.0) + _rrf(rank) * 2.0

    col_table_seen: Dict[str, int] = {}
    for _, c in col_hits:
        tbl = c["table"]
        rank = col_table_seen.get(tbl, 0)
        col_table_seen[tbl] = rank + 1
        all_table_meta.setdefault(tbl, {"table": tbl})
        scores[tbl] = scores.get(tbl, 0.0) + _rrf(rank) * 1.5

    label_table_seen: Dict[str, int] = {}
    for _, lbl in label_hits:
        tbl = lbl["table"]
        rank = label_table_seen.get(tbl, 0)
        label_table_seen[tbl] = rank + 1
        all_table_meta.setdefault(tbl, {"table": tbl})
        scores[tbl] = scores.get(tbl, 0.0) + _rrf(rank) * 1.0

    ranked_tables = sorted(scores, key=scores.__getitem__, reverse=True)[:TOP_K_TABLES]

    # ── Resolve return_id live via this app's own XML — never trust whatever
    # (if anything) the embedding metadata itself carries for return_id/name.
    for tbl in ranked_tables:
        meta = all_table_meta[tbl]
        ret = return_lookup.get_return_for_table(tbl)
        if ret:
            meta.update(ret)
        else:
            meta.setdefault("return_id", None)
            meta.setdefault("return_name", None)

    # ── Authorization filter — reuse the existing, untouched auth function ────
    if not AUTH_ENABLED:
        logger.warning(
            "[nlp.retriever] AUTH_DISABLED — skipping return-access filtering for query=%r",
            query,
        )
        tables = [all_table_meta[tbl] for tbl in ranked_tables]
    else:
        allowed_returns = get_allowed_form_ids(login_id) or set()

        def _is_authorized(table_meta: Dict[str, Any]) -> bool:
            rid = table_meta.get("return_id")
            return rid is not None and str(rid) in allowed_returns

        tables = [all_table_meta[tbl] for tbl in ranked_tables if _is_authorized(all_table_meta[tbl])]
    authorized_table_names = {t["table"] for t in tables}

    if len(tables) < len(ranked_tables):
        logger.info(
            "[nlp.retriever] login_id=%r | dropped %d unauthorized table(s) from shortlist",
            login_id, len(ranked_tables) - len(tables),
        )

    columns = [c for _, c in col_hits if c["table"] in authorized_table_names]
    seen_cols = set()
    unique_columns = []
    for c in columns:
        key = (c["table"], c["column"])
        if key not in seen_cols:
            seen_cols.add(key)
            unique_columns.append(c)
    columns = unique_columns[: TOP_K_COLUMNS * 2]

    matched_labels = [lbl for _, lbl in label_hits if lbl["table"] in authorized_table_names]
    seen_labels = set()
    unique_labels = []
    for lbl in matched_labels:
        key = (lbl["table"], lbl["column"], lbl["value"])
        if key not in seen_labels:
            seen_labels.add(key)
            unique_labels.append(lbl)
    matched_labels = unique_labels[:TOP_K_LABELS]

    logger.info(
        "[nlp.retriever] query=%r | login_id=%r | %d authorized table(s), %d column(s), %d label(s)",
        query, login_id, len(tables), len(columns), len(matched_labels),
    )
    return {"tables": tables, "columns": columns, "matched_labels": matched_labels}
