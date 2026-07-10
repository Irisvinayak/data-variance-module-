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
from typing import Any, Dict, List, Optional

import requests

from .nlp_config import INTENT_MODEL, INTENT_NUM_PREDICT, OLLAMA_TIMEOUT_SEC, OLLAMA_URL

logger = logging.getLogger(__name__)

# Reused across calls — avoids a fresh TCP/TLS handshake per Ollama request
# (this module makes up to 2 calls per query: initial attempt + one retry).
_session = requests.Session()

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

    lines = []
    for t in tables:
        table_cols = [c["column"] for c in columns if c["table"] == t["table"]]
        cols_str = ", ".join(table_cols) if table_cols else "(no candidate columns retrieved)"
        lines.append(
            f"- return_id={t.get('return_id')} return_name={t.get('return_name')!r} "
            f"table_name={t['table']!r}\n  candidate columns: {cols_str}"
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
- If the question implies a combined/total metric and both a "_DOM" and "_OVE" variant of the
  same column exist among the candidates, include both.

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
        table_cols = [c["column"] for c in columns if c["table"] == t["table"]]
        cols_str = ", ".join(table_cols) if table_cols else "(none)"
        lines.append(f"- return_id={t.get('return_id')} table_name={t['table']!r} columns: {cols_str}")
    candidates_block = "\n".join(lines)

    return f"""Your previous answer was invalid: {bad_raw[:200]!r}

It must be ONLY a JSON object using values copied verbatim from these candidates — nothing else:
{candidates_block}

Question: "{query}"

Output ONLY: {{"return_id": "<return_id>", "table_name": "<table_name>", "selected_columns": ["<column>", ...]}}"""


def _call_ollama(prompt: str) -> str:
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
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error("[nlp.intent_resolver] Ollama call failed | model=%s | %s", INTENT_MODEL, exc)
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    text = response.json().get("response", "")
    logger.info("[nlp.intent_resolver] Ollama responded | model=%s | response_chars=%d", INTENT_MODEL, len(text))
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
    if table_meta is None or str(table_meta.get("return_id")) != return_id:
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


def resolve_intent(query: str, shortlist: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Return {"return_id", "table_name", "selected_columns"} grounded entirely
    in `shortlist`, or None if the LLM couldn't produce a valid resolution
    after one retry."""
    if not shortlist["tables"]:
        return None

    prompt = _build_prompt(query, shortlist)

    try:
        raw = _call_ollama(prompt)
    except RuntimeError as exc:
        logger.error("[nlp.intent_resolver] %s", exc)
        return None

    parsed = _parse_json_response(raw)
    resolved = _validate_grounding(parsed, shortlist) if parsed else None

    if resolved is None:
        logger.warning("[nlp.intent_resolver] First attempt ungrounded/invalid (%r), retrying once", raw[:200])
        retry_prompt = _build_retry_prompt(query, shortlist, raw)
        try:
            raw = _call_ollama(retry_prompt)
            parsed = _parse_json_response(raw)
            resolved = _validate_grounding(parsed, shortlist) if parsed else None
        except RuntimeError as exc:
            logger.error("[nlp.intent_resolver] Retry failed: %s", exc)
            return None

    if resolved is None:
        logger.warning("[nlp.intent_resolver] Could not resolve query=%r to a grounded intent", query)

    return resolved
