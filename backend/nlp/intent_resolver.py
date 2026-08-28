# intent_resolver.py — LLM-assisted step: authorized shortlist -> structured
# intent. Deliberately does NOT ask the LLM for raw SQL text (unlike
# sql_agent's sql_generator.py) — backend/db.py executes SQL with zero
# parameterization, so letting an LLM author free-form SQL against the live
# Oracle connection would reopen an injection surface. Instead the LLM picks
# from — and is validated against — the already-authorized shortlist only;
# the actual SQL is produced afterwards by the existing, unmodified
# calculate_variance.build_query() via service.compute_variance().
#
# Uses INTENT_MODEL (a general instruction-following chat model, e.g.
# qwen2.5:7b), NOT the sql_generator.py model — a SQL-completion specialist
# like sqlcoder is trained to continue SQL text, not to follow "respond with
# JSON only" instructions, and reliably fails this task.

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from .nlp_config import (
    INTENT_FAILURE_COOLDOWN_SEC,
    INTENT_MAX_COLS_PER_TABLE,
    INTENT_MODEL,
    INTENT_NUM_PREDICT,
    INTENT_TIMEOUT_SEC,
    OLLAMA_URL,
)

logger = logging.getLogger(__name__)

# Reused across calls — avoids a fresh TCP/TLS handshake per Ollama request
# (this module makes up to 2 calls per query: initial attempt + one retry).
_session = requests.Session()

# Circuit-breaker state — set when a call fails at the TRANSPORT level
# (unreachable/timed out), so subsequent requests skip the LLM and use the
# deterministic resolver immediately instead of each paying the timeout over
# again. See INTENT_FAILURE_COOLDOWN_SEC.
_cooldown_until: float = 0.0
_cooldown_lock = threading.Lock()


class _TransportError(RuntimeError):
    """Ollama was unreachable/timed out — as opposed to answering badly.
    Distinguished because retrying is pointless (and doubles worst-case
    latency) when nothing is answering, whereas a malformed answer from a
    live model is worth exactly one retry."""

_FEW_SHOT_EXAMPLE = """Example:
Candidates:
- return_id=2041 return_name='CIMS_RAQ(Quarterly)' table_name='CIMS_RAQ_Q_SEC1_PART_A_DOM'
  candidate columns: TOTAL_LOAN_ASSETS, TERM_LOAN, CODE, RDATE
- return_id=2041 return_name='CIMS_RAQ(Quarterly)' table_name='CIMS_RAQ_Q_SEC3_PART_A'
  candidate columns: GROSS_NPA, NET_NPA, CODE, RDATE

Question: "total loan assets"

Answer:
{"return_id": "2041", "table_name": "CIMS_RAQ_Q_SEC1_PART_A_DOM", "selected_columns": ["TOTAL_LOAN_ASSETS"]}"""


def _build_prompt(query: str, shortlist: Dict[str, List[Dict[str, Any]]]) -> str:
    tables = shortlist["tables"]
    columns = shortlist["columns"]
    matched_labels = shortlist.get("matched_labels") or []

    lines = []
    for t in tables:
        # Capped — see INTENT_MAX_COLS_PER_TABLE. Columns arrive similarity-
        # ranked, and _validate_grounding still accepts anything in the full
        # shortlist, so this bounds prompt size (and generation latency)
        # without narrowing what's selectable.
        table_cols = [c["column"] for c in columns if c["table"] == t["table"]][
            :INTENT_MAX_COLS_PER_TABLE
        ]
        cols_str = ", ".join(table_cols) if table_cols else "(no candidate columns retrieved)"
        table_labels = [lbl for lbl in matched_labels if lbl["table"] == t["table"]]
        labels_line = ""
        if table_labels:
            labels_str = ", ".join(f'{lbl["column"]}={lbl["value"]!r}' for lbl in table_labels)
            labels_line = f"\n  matched row values: {labels_str}"
        lines.append(
            f"- return_id={t.get('return_id')} return_name={t.get('return_name')!r} "
            f"table_name={t['table']!r}\n  candidate columns: {cols_str}{labels_line}"
        )
    candidates_block = "\n".join(lines)

    return f"""You are a query router for a banking regulatory-return data variance tool.
Given a user's question and a shortlist of candidate return/table/column combinations
already selected by semantic search, choose the ONE table (and its return) that best
answers the question, and the column(s) within it that the user is asking about.

{_FEW_SHOT_EXAMPLE}

Now do the same for this real request.

Candidates:
{candidates_block}

Question: "{query}"

Rules:
- return_id and table_name MUST be copied verbatim from the candidates above — never invent new ones.
- Every entry in selected_columns MUST be one of that table's candidate columns listed above.
- Pick exactly ONE table. Do not combine multiple tables.
- If the question does NOT name a specific scope (domestic/overseas/global) and both a "_DOM" and
  "_OVE" variant of the same column exist among the candidates, include both — the question is
  implicitly asking for the combined/total figure.
- If the question DOES name a specific scope (e.g. "for domestic", "overseas only"), select ONLY
  the column variant matching that scope — do not also include the other side just because a
  "total" word appears elsewhere in the question.
- If a table lists "matched row values" and one of them matches a term in the question (e.g.
  RISK_CATEGORY='Standard' matching the word "standard"), STRONGLY prefer that table over any
  other table whose column NAME merely happens to contain the same word — a row-value match is
  stronger evidence than a column-name coincidence. In that case, select the amount/value column(s)
  associated with that row-label column (e.g. OUTSTANDING_AMT_DOM/OUTSTANDING_AMT_O alongside
  RISK_CATEGORY), not an unrelated column that just shares the word.

Output ONLY the JSON object below — the very first character of your response must be "{{"
and the very last must be "}}". No markdown, no code fences, no explanation, no extra text
before or after it.
{{"return_id": "<return_id>", "table_name": "<table_name>", "selected_columns": ["<column>", ...]}}"""


def _build_retry_prompt(query: str, shortlist: Dict[str, List[Dict[str, Any]]], bad_raw: str) -> str:
    """Shorter than a full re-send of the original prompt — no few-shot
    example or rules prose, just what's needed to correct course. Each
    Ollama call is stateless (no conversation memory), so the candidates
    must still be repeated in full."""
    tables = shortlist["tables"]
    columns = shortlist["columns"]
    lines = []
    for t in tables:
        table_cols = [c["column"] for c in columns if c["table"] == t["table"]][
            :INTENT_MAX_COLS_PER_TABLE
        ]
        cols_str = ", ".join(table_cols) if table_cols else "(none)"
        lines.append(f"- return_id={t.get('return_id')} table_name={t['table']!r} columns: {cols_str}")
    candidates_block = "\n".join(lines)

    return f"""Your previous answer was invalid: {bad_raw[:200]!r}

It must be ONLY a JSON object using values copied verbatim from these candidates — nothing else:
{candidates_block}

Question: "{query}"

Output ONLY: {{"return_id": "<return_id>", "table_name": "<table_name>", "selected_columns": ["<column>", ...]}}"""


def _in_cooldown() -> bool:
    with _cooldown_lock:
        return time.monotonic() < _cooldown_until


def _start_cooldown() -> None:
    global _cooldown_until
    with _cooldown_lock:
        _cooldown_until = time.monotonic() + INTENT_FAILURE_COOLDOWN_SEC
    logger.warning(
        "[nlp.intent_resolver] Ollama unreachable — skipping LLM intent resolution "
        "for the next %.0fs (deterministic fallback in use)",
        INTENT_FAILURE_COOLDOWN_SEC,
    )


def _call_ollama(prompt: str) -> str:
    started = time.monotonic()
    logger.info("[nlp.intent_resolver] Calling Ollama | model=%s | prompt_chars=%d", INTENT_MODEL, len(prompt))
    try:
        response = _session.post(
            OLLAMA_URL,
            json={
                "model": INTENT_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": INTENT_NUM_PREDICT},
            },
            timeout=INTENT_TIMEOUT_SEC,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error(
            "[nlp.intent_resolver] Ollama call failed after %.1fs | model=%s | %s",
            time.monotonic() - started, INTENT_MODEL, exc,
        )
        raise _TransportError(f"Ollama request failed: {exc}") from exc

    text = response.json().get("response", "")
    logger.info(
        "[nlp.intent_resolver] Ollama responded in %.1fs | model=%s | response_chars=%d",
        time.monotonic() - started, INTENT_MODEL, len(text),
    )
    return text


def _parse_json_response(raw: str) -> Optional[Dict[str, Any]]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _validate_grounding(
    parsed: Dict[str, Any], shortlist: Dict[str, List[Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    tables_by_name = {t["table"]: t for t in shortlist["tables"]}
    return_id = str(parsed.get("return_id", "")).strip()
    table_name = str(parsed.get("table_name", "")).strip()
    selected_columns = parsed.get("selected_columns") or []

    table_meta = tables_by_name.get(table_name)
    if table_meta is None:
        return None

    # A table whose own return_id never resolved (return_lookup.get_return_for_table()
    # found no table-mapping file for it — see return_lookup.py) has return_id=None.
    # str(None) == "None" would otherwise coincidentally "match" an LLM response of
    # {"return_id": null, ...} for the same table, passing validation with a
    # meaningless return_id that can never be looked up downstream — producing a
    # confusing 404 far away from the actual cause. Reject it here, explicitly.
    table_return_id = table_meta.get("return_id")
    if table_return_id is None or str(table_return_id) != return_id:
        return None

    valid_cols = {c["column"] for c in shortlist["columns"] if c["table"] == table_name}
    clean_columns = [c for c in selected_columns if isinstance(c, str) and c in valid_cols]
    if not clean_columns:
        return None

    return {
        "return_id": return_id,
        "table_name": table_name,
        "selected_columns": clean_columns,
    }


# Scope-suffix pairs (domestic / overseas) used by the deterministic
# resolver to mirror the prompt's own _DOM/_OVE rules — see _build_prompt's
# Rules section. Column pairs in this schema look like TOT_EXPO_DOM /
# TOT_EXPO_OVE, or OUTSTANDING_AMT_DOM / OUTSTANDING_AMT_O.
_SCOPE_SUFFIX_RE = re.compile(r"_(DOM|DOMESTIC|D|OVE|OVERSEAS|OVS|O)$", re.IGNORECASE)
_DOM_SUFFIXES = ("DOM", "DOMESTIC", "D")
_QUERY_DOM_RE = re.compile(r"\bdom(?:estic)?\b|\bindia\b", re.IGNORECASE)
_QUERY_OVE_RE = re.compile(r"\bove(?:rseas)?\b|\bforeign\b|\boffshore\b", re.IGNORECASE)


_WORD_RE = re.compile(r"[a-z0-9]+")


def _rank_columns_by_query(query: str, columns: List[str]) -> List[str]:
    """Re-rank candidate columns by literal token overlap with the query.

    Needed because the two shortlist sources order columns differently:
    retriever.py's own search returns them similarity-ranked (so position 0
    is already the best guess), but main.py's _shortlist_for_return /
    _shortlist_for_table pull a table's COMPLETE column list straight from
    the index with no query-scoped ranking at all — there, position 0 is
    just whatever column happens to come first in the table (e.g.
    PERIOD_DELINQUENCY, a row label, beating TOTAL_LOAN_ASSETS for the query
    "total loan assets").

    Sorted by (overlapping token count, then share of the column's own
    tokens matched) so a full match like TOTAL_LOAN_ASSETS outranks both a
    partial one (TERM_LOAN) and an incidental short one (LOAN). The sort is
    stable and keyed only on these scores, so columns the query says nothing
    about keep their incoming order — preserving the similarity ranking on
    the retriever path.
    """
    q_tokens = set(_WORD_RE.findall(query.lower()))
    if not q_tokens:
        return columns

    def score(column: str):
        c_tokens = set(_WORD_RE.findall(column.lower()))
        if not c_tokens:
            return (0, 0.0)
        overlap = q_tokens & c_tokens
        return (len(overlap), len(overlap) / len(c_tokens))

    return sorted(columns, key=lambda c: score(c), reverse=True)


def _scope_base(column: str) -> str:
    return _SCOPE_SUFFIX_RE.sub("", column.upper())


def _scope_of(column: str) -> Optional[str]:
    m = _SCOPE_SUFFIX_RE.search(column.upper())
    if not m:
        return None
    return "DOM" if m.group(1).upper() in _DOM_SUFFIXES else "OVE"


def _resolve_deterministic(
    query: str, shortlist: Dict[str, List[Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    """LLM-free resolution straight from the retrieval scores.

    This exists because the LLM's entire job in this module is to pick ONE
    table from an already-ranked shortlist plus which of its columns — and
    retriever.py has already done that ranking (fused RRF over column /
    row-label / table-description / BM25 signals, with `tables` and
    `columns` both returned in descending relevance order). So the top-
    ranked table and its top-ranked column ARE the retrieval layer's own
    answer; the LLM is a refinement on top, not the only thing capable of
    producing a result.

    Used whenever the LLM can't be reached or won't produce grounded output,
    so a transient Ollama problem degrades result QUALITY slightly instead of
    failing the whole request with a 404 (which is what
    "Could not resolve this query to a known table/column" was). Runs in
    microseconds — no network, no model.

    Mirrors the prompt's scope rules: if the winning column is one half of a
    _DOM/_OVE pair and the query names no scope, both halves are returned
    (the implicit "combined total"); if the query does name a scope, only
    that half is.
    """
    tables = shortlist.get("tables") or []
    all_columns = shortlist.get("columns") or []

    def _cols_for(name: str) -> List[str]:
        return [c["column"] for c in all_columns if c["table"] == name]

    # Prefer the highest-ranked table that actually has candidate columns —
    # a table with none can still be computed (see below), but one with
    # columns lets the result be trimmed to what was asked for.
    chosen = next(
        (t for t in tables if t.get("return_id") is not None and _cols_for(t["table"])),
        None,
    )
    if chosen is None:
        # Nothing had columns — fall back to the top table with a resolvable
        # return_id. selected_columns is left EMPTY on purpose: it only ever
        # trims the result (compute_variance always computes every numeric
        # column, and main.py's _restrict_result_columns returns the result
        # unchanged for an empty list), so this yields the full table rather
        # than the 404 that "no candidate columns" used to cause.
        chosen = next((t for t in tables if t.get("return_id") is not None), None)
    if chosen is None:
        return None

    table_name = chosen["table"]
    return_id = chosen["return_id"]
    table_columns = _cols_for(table_name)

    if not table_columns:
        logger.warning(
            "[nlp.intent_resolver] No candidate columns for any shortlisted table "
            "(top=%s) — resolving to the whole table, result will not be column-trimmed",
            table_name,
        )
        return {
            "return_id": str(return_id),
            "table_name": table_name,
            "selected_columns": [],
        }

    winner = _rank_columns_by_query(query, table_columns)[0]
    selected = [winner]

    scope = _scope_of(winner)
    if scope is not None:
        base = _scope_base(winner)
        siblings = [c for c in table_columns if _scope_base(c) == base]
        wants_dom = bool(_QUERY_DOM_RE.search(query))
        wants_ove = bool(_QUERY_OVE_RE.search(query))
        if wants_dom or wants_ove:
            want = "DOM" if wants_dom and not wants_ove else "OVE"
            scoped = [c for c in siblings if _scope_of(c) == want]
            selected = scoped or [winner]
        else:
            # No scope named -> the question implicitly wants the combined
            # figure, same rule the LLM prompt states.
            selected = siblings

    resolution = {
        "return_id": str(return_id),
        "table_name": table_name,
        "selected_columns": selected,
    }
    logger.info(
        "[nlp.intent_resolver] Deterministic resolution (no LLM) | query=%r | "
        "table=%s | columns=%s",
        query, table_name, selected,
    )
    return resolution


def resolve_intent(query: str, shortlist: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Return {"return_id", "table_name", "selected_columns"} grounded entirely
    in `shortlist`.

    Only returns None when `shortlist` itself carries nothing usable (no
    tables, or no columns for the top table). An LLM failure of ANY kind —
    unreachable endpoint, timeout, malformed/ungrounded output — falls back
    to _resolve_deterministic() rather than failing the request, since the
    retrieval layer's own ranking is a perfectly serviceable answer and a
    slightly-less-refined result beats a 404.
    """
    if not shortlist["tables"]:
        return None

    # Endpoint known-bad right now -> don't spend the timeout rediscovering
    # that on every single request.
    if _in_cooldown():
        logger.info("[nlp.intent_resolver] In Ollama cooldown — using deterministic resolution")
        return _resolve_deterministic(query, shortlist)

    prompt = _build_prompt(query, shortlist)

    try:
        raw = _call_ollama(prompt)
    except _TransportError as exc:
        # Nothing answered — a retry would just pay the timeout twice.
        logger.error("[nlp.intent_resolver] %s", exc)
        _start_cooldown()
        return _resolve_deterministic(query, shortlist)

    parsed = _parse_json_response(raw)
    resolved = _validate_grounding(parsed, shortlist) if parsed else None

    if resolved is None:
        # The model IS alive, it just answered badly — worth exactly one
        # corrective retry (the retry prompt is much shorter than the
        # original, so this is cheap).
        logger.warning("[nlp.intent_resolver] First attempt ungrounded/invalid (%r), retrying once", raw[:200])
        retry_prompt = _build_retry_prompt(query, shortlist, raw)
        try:
            raw = _call_ollama(retry_prompt)
            parsed = _parse_json_response(raw)
            resolved = _validate_grounding(parsed, shortlist) if parsed else None
        except _TransportError as exc:
            logger.error("[nlp.intent_resolver] Retry failed: %s", exc)
            _start_cooldown()
            return _resolve_deterministic(query, shortlist)

    if resolved is None:
        logger.warning(
            "[nlp.intent_resolver] LLM could not ground query=%r after retry — "
            "falling back to deterministic resolution",
            query,
        )
        return _resolve_deterministic(query, shortlist)

    return resolved
