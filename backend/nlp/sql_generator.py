# sql_generator.py — LLM writes the actual SQL. Ports sql_agent/src/sql_generator.py
# almost verbatim (same Oracle/CRILC domain, same prompt rules) for the NEW
# /variance/nlquery endpoint. Unlike backend/nlp/intent_resolver.py (which backs
# the existing /variance/nlresolve and never lets the LLM see/write raw SQL),
# this module hands the model real SQL-writing power — so validate_sql() here
# is the actual security boundary: it rejects anything that isn't a SELECT,
# contains a DML/DDL keyword, or references a table/column outside the
# authorized shortlist the caller passed in (that shortlist was already
# filtered to the user's allowed returns by retriever.get_relevant_schema()).

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import date
from typing import Any, Optional

import requests

from .. import ai_audit
from .nlp_config import (
    DESCRIPTION_SAMPLES_PATH,
    MODEL_PROFILES,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SEC,
    OLLAMA_URL,
    SCHEMA_JSON_PATH,
)

logger = logging.getLogger(__name__)

# Reused across calls — avoids a fresh TCP/TLS handshake per Ollama request
# (generate_sql makes up to 2 calls per query: initial attempt + one retry).
_session = requests.Session()


# mtime-keyed in-memory cache, same pattern as index_store.py/lexical_search.py/
# return_lookup.py — schema.json/description_samples.json only change when the
# external build tool rebuilds them, so re-reading+re-parsing from disk on
# every call (generate_sql() triggers up to 4 reads of schema.json alone: one
# from build_prompt, one from validate_sql, doubled again on the one retry) is
# pure waste that grows with total schema size as the corpus scales up.
_json_cache: dict[str, Any] = {}
_json_cache_lock = threading.Lock()


def _load_json_cached(path: str, default: Any) -> Any:
    if not os.path.isfile(path):
        return default

    mtime = os.path.getmtime(path)
    with _json_cache_lock:
        cached = _json_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    with _json_cache_lock:
        _json_cache[path] = (mtime, data)
    return data


def load_samples(path: str = DESCRIPTION_SAMPLES_PATH) -> dict[str, dict[str, list[str]]]:
    """Load the full row-label samples dict the external build tool produced
    (INDEX_DIR/description_samples.json) — supplements the FAISS top-K
    matches with every known label value for a matched table. Returns {} if
    the file doesn't exist. Cached by file mtime — a freshly rebuilt file is
    picked up on the next call, no restart needed."""
    return _load_json_cached(path, {})


BANNED_KEYWORDS = [
    "delete", "update", "drop", "insert", "truncate", "alter", "create", "exec",
    "execute", "merge", "grant", "revoke", "call", "lock", "commit", "rollback",
    "savepoint", "declare", "begin", "rename", "purge",
]

# Constructs no legitimate single SELECT against the shortlist needs, each of
# which reaches past it: statement chaining, comments that can hide the rest of
# a query, database links, and the Oracle packages that read files or make
# network calls from inside a SELECT (UTL_HTTP, UTL_FILE, DBMS_*, ...).
_BANNED_PATTERNS = [
    (re.compile(r";"), "statement separator ';'"),
    (re.compile(r"--|/\*"), "SQL comment"),
    (re.compile(r"@"), "database link '@'"),
    (re.compile(r"\b(?:dbms_|utl_|sys\.|ctxsys\.|httpuritype|xmltype)"), "Oracle system package"),
]

# The item list after a FROM, up to the next clause keyword or closing paren.
# Every comma-separated item's leading identifier is a table reference: the
# FROM/JOIN regex alone sees only the first one of "FROM allowed_t, other_t".
_FROM_LIST_RE = re.compile(
    r"\bfrom\s+(.*?)(?=\bwhere\b|\bgroup\b|\border\b|\bhaving\b|\bunion\b"
    r"|\bintersect\b|\bminus\b|\bfetch\b|\bconnect\b|\bstart\b|\)|$)",
    re.S,
)
# Table references include `$`/`#` and an optional `schema.` prefix so that
# "FROM v$session" or "FROM other_schema.allowed_t" is seen whole and rejected,
# rather than truncated to a prefix that happens to match the allowlist.
_TABLE_IDENT = r"[a-z_][a-z0-9_$#]*(?:\s*\.\s*[a-z_][a-z0-9_$#]*)?"
_LEADING_IDENT_RE = re.compile(rf"\s*({_TABLE_IDENT})")
_FROM_JOIN_RE = re.compile(rf"(?:from|join)\s+({_TABLE_IDENT})")
MAX_LABELS_MINIMAL = 8
# Caps the row-label values load_samples() injects per column: a wide table
# with hundreds of label values would otherwise balloon the prompt until
# OLLAMA_TIMEOUT_SEC fails the whole request.
# More generous than MAX_LABELS_MINIMAL since "rules" style is meant to carry
# richer context, but still bounded.
MAX_LABELS_RULES = 25


def _resolve_relative_time(query: str, today: date, fiscal_start_month: int = 4) -> Optional[str]:
    """Resolve the query's date phrases to concrete calendar ranges, injected
    into the prompt so the LLM never has to guess what "last quarter" or
    "Q1FY25" means. Parsed by date_intent — the same reader /variance/nlresolve
    uses, so the two endpoints agree. `today` should be the table's newest
    data date (see variance_nlquery): anchoring on the wall clock pointed
    "last quarter" at a quarter the data had not reached yet."""
    from .date_intent import UNIT_MONTHS, parse_date_intent, period_containing, step_back

    intent = parse_date_intent(query, fiscal_start_month, today=today)
    if intent.is_empty:
        return None

    def fmt(d):
        return d.strftime("%Y-%m-%d")

    def rng(s, e):
        return f"{fmt(s)} to {fmt(e)}"

    def unit_period(d, unit):
        if unit in UNIT_MONTHS or unit in ("day", "week"):
            return period_containing(d, unit, fiscal_start_month)
        return d, d

    lines = [f"latest reporting date in the data = {fmt(today)}"]
    for ref in ([intent.anchor] if intent.anchor else []) + intent.targets:
        lines.append(f"'{ref.text.strip()}' = {rng(ref.start, ref.end)}")
    if intent.range:
        lo, hi = intent.range
        lines.append(f"'{lo.text.strip()} .. {hi.text.strip()}' = {rng(lo.start, hi.end)}")
    if intent.since:
        lines.append(f"'since {intent.since.text.strip()}' = {rng(intent.since.start, today)}")
    base = intent.anchor.end if intent.anchor else today
    if intent.relative:
        n, unit = intent.relative
        if unit == "period":
            lines.append(f"'last {n} periods' = the {n} most recent distinct RDATE values up to {fmt(base)}")
        else:
            # The n units ending with the one that contains the anchor date.
            s, _e = unit_period(step_back(base, unit, n - 1), unit)
            _s, e = unit_period(base, unit)
            lines.append(f"'last {n} {unit}(s)' = {rng(s, min(e, base))}")
    if intent.xox:
        prev = step_back(base, intent.xox, 1)
        lines.append(
            f"'{intent.xox}-over-{intent.xox}' = current RDATE {fmt(base)} vs previous "
            f"RDATE on or before {fmt(prev)}"
        )
    if intent.this_unit:
        s, e = unit_period(today, intent.this_unit)
        lines.append(f"'this {intent.this_unit}' = {rng(s, e)}")

    block = (
        "════════════════════════════════════════════════\n"
        "RESOLVED TIME CONTEXT\n"
        "(these are the EXACT date ranges for the relative terms the user wrote)\n"
        "════════════════════════════════════════════════\n"
    )
    block += "\n".join(f"  {l}" for l in lines)
    block += "\n"
    return block


_SQL_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null",
    "as", "on", "join", "inner", "outer", "left", "right", "full", "cross",
    "group", "by", "order", "having", "distinct", "between", "like", "case",
    "when", "then", "else", "end", "union", "all", "exists", "limit", "offset",
    "count", "sum", "avg", "min", "max", "coalesce", "nvl", "trim", "upper",
    "lower", "to_date", "to_char", "rownum", "dual", "with", "asc", "desc",
    "over", "partition", "rows", "range", "unbounded", "preceding",
    "following", "current", "row", "window", "rank", "dense_rank",
    "row_number", "ntile", "lag", "lead", "first_value", "last_value",
}


def _load_all_columns(table_names, schema_path: str = SCHEMA_JSON_PATH):
    """Return all columns for the given table names, loaded from schema.json
    (produced by the external embedding-build tool, dropped into
    INDEX_DIR) — the LLM sees every column of a matched table, not
    just the top-K the embedding retrieval happened to surface. schema.json
    itself is cached by mtime (see _load_json_cached) — this function still
    re-filters per call since the requested table_names differ per call, but
    no longer re-reads+re-parses the (potentially large, corpus-sized) file
    from disk every time."""
    schema = _load_json_cached(schema_path, [])
    if not schema:
        return []
    normalized_table_names = {name.lower() for name in table_names}
    result = []
    for entry in schema:
        table_name = entry.get("table") or entry.get("table_name")
        if not table_name or table_name.lower() not in normalized_table_names:
            continue
        for col in entry.get("columns") or []:
            column_name = col.get("name") or col.get("column_name")
            if not column_name:
                continue
            result.append({"table": table_name, "column": column_name})
    return result


def _get_model_profile(model_name):
    default_profile = {
        "prompt_style": "rules", "dialect_hint": "Oracle",
        "supports_full_ruleset": True, "temperature": None, "num_predict": None,
    }
    return {**default_profile, **MODEL_PROFILES.get(model_name, {})}


def _validation_failure_category(reason: str) -> str:
    reason = reason.lower()
    if "dangerous keyword" in reason:
        return "banned keyword"
    if "hallucinated tables" in reason:
        return "hallucinated table"
    if "hallucinated columns" in reason:
        return "hallucinated column"
    if "does not reference any matched table" in reason:
        return "schema grounding"
    return "unknown validation"


def _build_full_rules_block(dialect_hint: str) -> str:
    return f"""You are an expert {dialect_hint} SQL generator.
Use {dialect_hint} syntax, including Oracle-specific constructs such as FETCH FIRST N ROWS ONLY, ADD_MONTHS, EXTRACT(YEAR FROM ...), and TO_DATE(...,'YYYY-MM-DD').

════════════════════════════════════════════════
ABSOLUTE RULES (never break these)
════════════════════════════════════════════════
1. Return ONLY a raw SQL SELECT query — no explanation, no markdown, no code fences, no semicolon.
2. Use ONLY table names and column names listed in the SCHEMA CONTEXT below. Never invent names.
3. Never use bind variables or placeholders (:val, ?, %s). Embed all values as literals.
4. Never touch backup tables (_bkup, _bk, _bckup, _backup suffixes). Use only the main tables.

════════════════════════════════════════════════
VERTICAL FORMAT TABLES — CRITICAL RULES
════════════════════════════════════════════════
Any table tagged "STORAGE FORMAT: VERTICAL" below stores data as named rows.
Each row is one pre-computed metric. The database already has aggregated/total rows — DO NOT re-aggregate them.

RULE V1 — NEVER aggregate vertically: use the exact row rather than SUM() across labeled rows.
RULE V2 — To get the TOTAL / OVERALL value, use WHERE <label_col> = '<*** TOTAL ROW ***>' shown for each table below.
RULE V3 — To get a SPECIFIC metric, match the label value CHARACTER FOR CHARACTER from the Known row labels list.
RULE V4 — LIKE fallback (only when no exact match is available): WHERE <label_col> LIKE '%keyword%'. Never invent or guess an exact string.
RULE V5 — You MAY use SUM/AVG only when the table is NOT tagged VERTICAL, or when aggregating across CODE/RDATE partitions on a non-label column.

════════════════════════════════════════════════
DOM / OVE COLUMN RULES (Domestic + Overseas)
════════════════════════════════════════════════
RULE D1 — "total"/"combined"/"overall" or unspecified domestic/overseas → add both columns: SELECT (EXPOSURE_DOM + EXPOSURE_OVE) AS TOTAL_EXPOSURE ...
RULE D2 — "domestic only" → <col>_DOM alone. "overseas only" → <col>_OVE alone.
RULE D3 — Comparing domestic vs overseas → select both columns separately.
RULE D4 — Do NOT invent a column named TOTAL_<x>; compute it inline as (<col>_DOM + <col>_OVE).

════════════════════════════════════════════════
MULTI-PART / MULTI-SECTION RULES
════════════════════════════════════════════════
RULE M1 — Combined/overall total spanning multiple parts of the SAME section → UNION ALL subquery, then SUM the result.
RULE M2 — Use UNION ALL (not UNION) to preserve all rows including duplicates.
RULE M3 — User mentions only one part explicitly → query ONLY that part.
RULE M4 — Unioning vertical tables → apply the SAME label WHERE filter in each branch.
RULE M5 — Same metric across multiple sections → UNION ALL with a literal section tag column.

════════════════════════════════════════════════
JOIN RULES
════════════════════════════════════════════════
RULE J1 — Use JOINs ONLY when the question requires data from multiple tables.
RULE J2 — Always join on CODE and RDATE together to avoid cross-joining reporting periods.
RULE J3 — For optional/possibly-missing rows use LEFT JOIN, not INNER JOIN.
RULE J4 — Never JOIN a main table with its own backup table.
RULE J5 — When joining two vertical tables, apply the WHERE label filter on BOTH sides.

════════════════════════════════════════════════
MULTI-CODE / BANK RULES
════════════════════════════════════════════════
RULE B1 — CODE identifies the reporting bank/entity. If user does not specify a bank, omit CODE filter.
RULE B2 — If user asks for a specific bank, filter WHERE CODE = <bank_code>.

════════════════════════════════════════════════
DATE & PERIOD RULES
════════════════════════════════════════════════
RULE P1 — RDATE is the reporting date column. Use it for all date-based filtering.
RULE P2 — "Latest"/"most recent"/"current" → WHERE RDATE = (SELECT MAX(RDATE) FROM <same_table>)
RULE P3 — "Last/past/previous N quarters/months/years/FY quarters/periods": if a RESOLVED TIME CONTEXT block below has a matching entry, use those EXACT dates verbatim.
RULE P4 — "For year YYYY" → WHERE EXTRACT(YEAR FROM RDATE) = YYYY
RULE P5 — "Between <date1> and <date2>" → WHERE RDATE BETWEEN TO_DATE('<date1>', 'YYYY-MM-DD') AND TO_DATE('<date2>', 'YYYY-MM-DD')
RULE P6 — "Trend"/"over time" → include RDATE in SELECT and GROUP BY, ORDER BY RDATE ASC.
RULE P7 — Never hardcode a date literal. Always derive latest date via MAX(RDATE).

════════════════════════════════════════════════
PERIOD-COMPARISON / VARIANCE RULES
════════════════════════════════════════════════
This application's core purpose is comparing the SAME metric across two
reporting periods (RDATE values) — this is different from RULE P6's "trend
over time" (which lists many periods in one result set). When the question
implies comparing exactly two periods — words like "compare", "variance",
"change", "growth", "increase", "decrease", "vs", "versus", or "between
<period1> and <period2>" — self-join the table to itself on CODE (and any
other identifier columns) across the two RDATE values and compute the
difference explicitly, rather than returning two separate result sets:

  SELECT curr.CODE,
         curr.<col> AS current_value,
         prev.<col> AS previous_value,
         (curr.<col> - prev.<col>) AS variance
  FROM <table> curr
  JOIN <table> prev
    ON curr.CODE = prev.CODE
  WHERE curr.RDATE = <current_period_date>
    AND prev.RDATE = <previous_period_date>

RULE C1 — Resolve <current_period_date>/<previous_period_date> from the RESOLVED
  TIME CONTEXT block if present; otherwise use MAX(RDATE) for "current"/"latest"
  and the next most recent distinct RDATE for "previous"/"last period".
RULE C2 — On VERTICAL tables, apply the SAME row-label WHERE filter to BOTH
  curr and prev — never compare unfiltered vertical rows.
RULE C3 — If the question does not ask for a comparison, do NOT self-join —
  answer directly with a single SELECT against RDATE.

════════════════════════════════════════════════
RANKING & TOP-N RULES
════════════════════════════════════════════════
RULE R1 — "Top N" → ORDER BY <col> DESC FETCH FIRST <N> ROWS ONLY
RULE R2 — "Bottom N" → ORDER BY <col> ASC FETCH FIRST <N> ROWS ONLY
RULE R3 — "Rank banks by <metric>" → RANK()/DENSE_RANK() window function.
RULE R4 — Never use ROWNUM for top-N unless there's no ORDER BY option; prefer FETCH FIRST.
"""


def _build_compressed_rules_block(dialect_hint: str) -> str:
    return f"""You are an expert {dialect_hint} SQL generator.
Use {dialect_hint} syntax, including Oracle-specific constructs such as FETCH FIRST N ROWS ONLY, ADD_MONTHS, EXTRACT(YEAR FROM ...), and TO_DATE(...,'YYYY-MM-DD').

════════════════════════════════════════════════
ABSOLUTE RULES
════════════════════════════════════════════════
- Return ONLY a raw SQL SELECT query: no explanation, no markdown, no code fences, no semicolon.
- Use ONLY table names and column names listed in the SCHEMA CONTEXT below.
- Never use bind variables or placeholders; embed literals directly.
- Never touch backup tables (_bkup, _bk, _bckup, _backup suffixes).
- If a RESOLVED TIME CONTEXT entry exists for the question, use those exact dates verbatim.

════════════════════════════════════════════════
KEY GUIDELINES
════════════════════════════════════════════════
- VERTICAL tables store named rows. Do not SUM vertical label rows across records.
- If STORAGE FORMAT: VERTICAL appears, filter by the exact label value, or LIKE only when no exact label exists.
- For DOM/OVE values, use _DOM or _OVE separately unless the user asks for combined totals.
- Use JOIN only when required, always join on CODE and RDATE together.
- Prefer Oracle top-N syntax and Oracle date syntax.
- Avoid inventing tables, columns, or business logic beyond the schema and resolved time context.
"""


def _build_minimal_prompt(dialect_hint, user_query, schema_context, time_context_block, valid_tables,
                           vertical_tables=None, dom_ove_tables=None, multipart_tables=None):
    time_section = f"\n{time_context_block}" if time_context_block else ""
    example_blocks = []

    if vertical_tables:
        example_blocks.append(
            "Example — vertical table:\nSELECT PERIOD_DELINQUENCY, TOTAL_LOAN_ASSETS\nFROM <table>\n"
            "WHERE PERIOD_DELINQUENCY = '<total row label>'"
        )
    if dom_ove_tables:
        example_blocks.append(
            "Example — domestic/overseas table:\nSELECT CODE, RDATE, EXPOSURE_DOM, EXPOSURE_OVE\nFROM <table>"
        )
    if multipart_tables:
        example_blocks.append(
            "Example — multi-part table:\nSELECT SUM(val) FROM "
            "(SELECT value_col AS val FROM <table_a> UNION ALL SELECT value_col AS val FROM <table_b>)"
        )
    if re.search(r'\b(compare|variance|change|growth|increase|decrease|vs\.?|versus)\b', user_query, re.IGNORECASE):
        example_blocks.append(
            "Example — comparing two periods (this app's core purpose):\n"
            "SELECT curr.CODE, curr.<col> AS current_value, prev.<col> AS previous_value,\n"
            "       (curr.<col> - prev.<col>) AS variance\n"
            "FROM <table> curr JOIN <table> prev ON curr.CODE = prev.CODE\n"
            "WHERE curr.RDATE = <current_period> AND prev.RDATE = <previous_period>"
        )

    examples_section = ""
    if example_blocks:
        examples_section = "\n\n### Few-shot examples\n" + "\n\n".join(example_blocks)

    return f"""You are an expert {dialect_hint} SQL generator.
Use {dialect_hint} syntax, including Oracle-specific constructs such as FETCH FIRST N ROWS ONLY, ADD_MONTHS, EXTRACT(YEAR FROM ...), and TO_DATE(...,'YYYY-MM-DD').

### Task
{user_query}

### Database Schema
{schema_context}

Allowed tables: {valid_tables}{time_section}{examples_section}

### Important
- If a table is marked STORAGE FORMAT: VERTICAL, each row is a named metric and you must filter by the exact row label values shown above.
- Do NOT aggregate vertical tables with SUM() across row labels unless the user explicitly asks for it.
- When the user requests a total or overall value, use the exact total row label provided in the schema context.
- Never invent row labels or column names that are not shown in the schema context.
- When comparing two periods, self-join the table on CODE across both RDATE values and compute the difference explicitly (see example above) rather than returning two separate result sets.

### Answer
Return ONLY a raw SQL SELECT query. No explanation, no markdown, no code fences, no semicolon.
"""


_TOTAL_ROW_KEYWORDS = [
    "total", "grand total", "sub-total", "subtotal",
    "all industries", "c. total", "c total", "grand-total",
    "i. gross", "iii. non-food", "ii. food",
]


def _find_total_row(values: list) -> Optional[str]:
    for v in values:
        vl = v.lower()
        if any(kw in vl for kw in _TOTAL_ROW_KEYWORDS):
            return v
    return None


def build_prompt(user_query, tables, columns, dialect="Oracle", today_date=None, matched_labels=None, model_name=None):
    if today_date is None:
        today_date = date.today().isoformat()
    if model_name is None:
        model_name = OLLAMA_MODEL
    profile = _get_model_profile(model_name)
    dialect_hint = profile.get("dialect_hint") or dialect
    prompt_style = profile.get("prompt_style", "rules")
    supports_full_ruleset = profile.get("supports_full_ruleset", True)

    table_names = {t["table"] for t in tables}
    all_columns = _load_all_columns(table_names)

    if matched_labels is None:
        matched_labels = []

    label_map: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for lbl in matched_labels:
        table_name = lbl["table"]
        column_name = lbl["column"]
        if prompt_style == "minimal":
            if len(label_map[table_name][column_name]) < MAX_LABELS_MINIMAL:
                label_map[table_name][column_name].append(lbl["value"])
        else:
            label_map[table_name][column_name].append(lbl["value"])

    if prompt_style == "rules":
        all_samples = load_samples()
        for tbl in table_names:
            if tbl in all_samples:
                for col, vals in all_samples[tbl].items():
                    existing = set(label_map[tbl][col])
                    for v in vals:
                        if len(label_map[tbl][col]) >= MAX_LABELS_RULES:
                            break
                        if v not in existing:
                            label_map[tbl][col].append(v)

    vertical_tables = set()
    dom_ove_tables = set()
    multipart_tables = set()
    for t in tables:
        table_name = t["table"]
        table_upper = table_name.upper()
        if table_name in label_map and label_map[table_name]:
            vertical_tables.add(table_name)
        if any(re.search(r'(_dom|_ove)\b', c["column"].upper()) for c in all_columns if c["table"].upper() == table_upper):
            dom_ove_tables.add(table_name)
        if re.search(r'_part_[ab](?:_|$)', table_name.lower()):
            multipart_tables.add(table_name)

    schema_lines = []
    for t in tables:
        table_name = t["table"].upper()
        table_cols = [c["column"].upper() for c in all_columns if c["table"].upper() == table_name]
        cols_str = ", ".join(table_cols) if table_cols else "(none)"
        block = f"Table: {table_name}\nAllowed columns (use ONLY these): {cols_str}"

        col_labels = label_map.get(t["table"], {})
        if col_labels:
            block += "\nSTORAGE FORMAT: VERTICAL — each row is a named metric. DO NOT aggregate with SUM() across all rows."
            label_lines = []
            for col, values in col_labels.items():
                sample_str = ", ".join(f"'{v}'" for v in values)
                label_lines.append(f"  {col.upper()} relevant values: {sample_str}")
                total_row = _find_total_row(values)
                if total_row:
                    label_lines.append(
                        f"  *** TOTAL ROW for {col.upper()}: '{total_row}' — "
                        f"use WHERE {col.upper()} = '{total_row}' when user asks for totals/overall/grand total ***"
                    )
            block += "\nRelevant row labels (matched to your query):\n" + "\n".join(label_lines)

        schema_lines.append(block)

    schema_context = "\n\n".join(schema_lines)
    valid_tables = ", ".join(t["table"].upper() for t in tables)

    today_obj = date.fromisoformat(today_date) if isinstance(today_date, str) else today_date
    from ..hosts import get_profile
    _time_block = _resolve_relative_time(
        user_query, today_obj, get_profile().fiscal_year_start_month,
    )
    time_context_block = (_time_block + "\n") if _time_block else ""

    if prompt_style == "minimal":
        return _build_minimal_prompt(
            dialect_hint, user_query, schema_context, time_context_block, valid_tables,
            vertical_tables=vertical_tables, dom_ove_tables=dom_ove_tables, multipart_tables=multipart_tables,
        )

    template = _build_full_rules_block(dialect_hint) if supports_full_ruleset else _build_compressed_rules_block(dialect_hint)
    return f"""{template}
════════════════════════════════════════════════
SCHEMA CONTEXT
════════════════════════════════════════════════
{schema_context}

════════════════════════════════════════════════
Allowed tables: {valid_tables}
{time_context_block}User question: {user_query}
════════════════════════════════════════════════
SQL:"""


def _strip_sql_fences(raw: str) -> str:
    """Strip ```sql fences and a trailing semicolon from a model response."""
    raw = re.sub(r'^```(?:sql)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r'```\s*$', '', raw).strip()
    return raw.rstrip().rstrip(';')


def _call_ollama(
    prompt_text: str, model_name: str, model_profile: dict, attempt: int = 1
) -> tuple:
    """Returns (response_text, latency_ms).

    `attempt` is logged so a retry is distinguishable from two interleaved
    requests.
    """
    options = {}
    if model_profile.get("temperature") is not None:
        options["temperature"] = model_profile["temperature"]
    if model_profile.get("num_predict") is not None:
        options["num_predict"] = model_profile["num_predict"]

    payload = {"model": model_name, "prompt": prompt_text, "stream": False}
    if options:
        payload["options"] = options

    logger.info(
        "[nlp.sql_generator] Calling Ollama | model=%s | attempt=%d | prompt_chars=%d",
        model_name, attempt, len(prompt_text),
    )
    started = time.monotonic()
    try:
        response = _session.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT_SEC)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "[nlp.sql_generator] Ollama call failed | model=%s | attempt=%d | ms=%d | %s",
            model_name, attempt, latency_ms, exc,
        )
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    text = response.json().get("response", "")
    logger.info(
        "[nlp.sql_generator] Ollama responded | model=%s | attempt=%d | ms=%d | response_chars=%d",
        model_name, attempt, latency_ms, len(text),
    )
    return text, latency_ms


def generate_sql(user_query, tables, columns, dialect="Oracle", today_date=None, matched_labels=None) -> dict[str, Any]:
    """Generate + validate a SQL SELECT for `user_query`, grounded strictly in
    `tables`/`columns` (the caller's already-authorized shortlist). Returns
    {"sql": str, "warnings": [str, ...]} — non-empty warnings means the SQL
    failed validation even after one retry and should NOT be executed."""
    prompt = build_prompt(user_query, tables, columns, dialect=dialect, today_date=today_date, matched_labels=matched_labels)
    model_name = OLLAMA_MODEL
    model_profile = _get_model_profile(model_name)

    # Fields common to every audit record this call emits. The model profile is
    # included because it changes the prompt materially (prompt_style,
    # temperature) — without it, two runs of the same model that behaved
    # differently are indistinguishable in the trail.
    audit = {
        "caller":       "sql_generator.generate_sql",
        "model":        model_name,
        "query":        user_query,
        "prompt_style": model_profile.get("prompt_style"),
        "temperature":  model_profile.get("temperature"),
        "prompt_chars": len(prompt),
    }

    try:
        raw, latency_ms = _call_ollama(prompt, model_name, model_profile, attempt=1)
    except RuntimeError as exc:
        logger.error("[nlp.sql_generator] %s", exc)
        ai_audit.record(**audit, attempt=1, ok=False, error=str(exc))
        return {"sql": "", "warnings": [str(exc)]}

    raw = _strip_sql_fences(raw)

    is_valid, reason = validate_sql(raw, tables, columns)
    warnings: list[str] = []
    retried = False

    if not is_valid:
        logger.warning(
            "[nlp.sql_generator] attempt 1 SQL rejected | model=%s | reason=%s",
            model_name, reason,
        )
        ai_audit.record(
            **audit, attempt=1, latency_ms=latency_ms, sql=raw,
            valid=False, validation_reason=reason,
            failure_category=_validation_failure_category(reason),
        )
        retried = True
        retry_prompt = (
            "The previous SQL was invalid. Return ONLY the corrected SQL.\n\n"
            f"Original prompt:\n{prompt}\n\n"
            f"Invalid SQL:\n{raw}\n\n"
            f"Validation reason:\n{reason}\n\n"
            "Corrected SQL:"
        )
        try:
            raw, latency_ms = _call_ollama(retry_prompt, model_name, model_profile, attempt=2)
        except RuntimeError as exc:
            logger.error("[nlp.sql_generator] Retry failed: %s", exc)
            ai_audit.record(**audit, attempt=2, ok=False, error=str(exc), retried=True)
            return {"sql": "", "warnings": [str(exc)]}
        raw = _strip_sql_fences(raw)
        is_valid, reason = validate_sql(raw, tables, columns)

    attempt = 2 if retried else 1

    if not is_valid:
        category = _validation_failure_category(reason)
        warning = f"Model '{model_name}' generated invalid SQL; probable failure category: {category}. Reason: {reason}"
        warnings.append(warning)
        logger.warning("[nlp.sql_generator] %s", warning)
        ai_audit.record(
            **audit, attempt=attempt, latency_ms=latency_ms, sql=raw,
            valid=False, retried=retried, validation_reason=reason,
            failure_category=category, warnings=warnings,
        )
        return {"sql": "", "warnings": warnings}

    ai_audit.record(
        **audit, attempt=attempt, latency_ms=latency_ms, sql=raw,
        valid=True, retried=retried, validation_reason=None, warnings=warnings,
    )
    return {"sql": raw, "warnings": warnings}


def validate_sql(sql, tables, columns):
    """Returns (is_valid, reason). `tables`/`columns` MUST be the caller's
    already-authorized shortlist — this function's table/column allowlist
    IS the authorization boundary for whatever SQL text actually runs."""
    if not sql:
        return False, "Empty SQL"

    q = sql.lower().replace('"', '').replace("'", '').strip()

    if not q.startswith("select"):
        return False, "Only SELECT queries are allowed"

    for pattern, label in _BANNED_PATTERNS:
        if pattern.search(q):
            logger.error(
                "[nlp.sql_generator] BANNED CONSTRUCT rejected | %s | sql=%r", label, sql,
            )
            return False, f"Disallowed construct: {label}"

    for word in BANNED_KEYWORDS:
        if re.search(rf'\b{word}\b', q):
            # The SQL-safety boundary firing, not an ordinary validation miss:
            # logged at ERROR so it is distinguishable from a column typo.
            logger.error(
                "[nlp.sql_generator] BANNED KEYWORD rejected | keyword=%r | sql=%r",
                word, sql,
            )
            return False, f"Dangerous keyword detected: '{word}'"

    valid_table_names = {t["table"].lower() for t in tables}
    all_columns = _load_all_columns(valid_table_names)
    valid_col_names = {c["column"].lower() for c in all_columns}

    subquery_aliases = set(re.findall(r'\bas\s+([a-z_][a-z0-9_]*)', q))
    subquery_aliases |= set(re.findall(r'\)\s+([a-z_][a-z0-9_]*)\b', q))

    q_for_tables = re.sub(r'\bextract\s*\([^)]*\)', '', q)
    q_for_tables = re.sub(r'\btrim\s*\([^)]*\)', '', q_for_tables)
    referenced_tables = set(_FROM_JOIN_RE.findall(q_for_tables))
    for from_list in _FROM_LIST_RE.findall(q_for_tables):
        for item in from_list.split(","):
            match = _LEADING_IDENT_RE.match(item)
            if match:
                referenced_tables.add(match.group(1))
    referenced_tables = {re.sub(r"\s+", "", t) for t in referenced_tables}
    # Aliases are NOT subtracted here: the query must start with SELECT (no
    # WITH clause), so nothing but a real table can stand in table position,
    # and subtracting them let "JOIN other_t AS other_t" or "(...) other_t
    # ... JOIN other_t" put an unauthorized table past the allowlist.
    hallucinated_tables = referenced_tables - valid_table_names
    if hallucinated_tables:
        return False, f"Hallucinated tables (not in schema): {sorted(hallucinated_tables)}"

    if not referenced_tables & valid_table_names:
        return False, f"Query does not reference any matched table: {sorted(valid_table_names)}"

    select_body = re.split(r'\bfrom\b', q_for_tables, maxsplit=1)[0]
    select_body = select_body.replace("select", "", 1).strip()
    select_body = re.sub(r'\bas\s+[a-z_][a-z0-9_]*', '', select_body)
    select_body = re.sub(
        r'\b(sum|avg|min|max|count|coalesce|nvl|nullif|trim|upper|lower|to_date|to_char)\s*\(', '(', select_body,
    )
    select_body = re.sub(r'[*/+\-()\[\]]', ' ', select_body)

    col_tokens = re.findall(r'(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)', select_body)

    hallucinated_cols = {
        t for t in col_tokens
        if (
            t not in valid_col_names
            and t not in _SQL_KEYWORDS
            and t not in subquery_aliases
            and t != "*"
            and not t.isdigit()
            and len(t) > 2
        )
    }
    if hallucinated_cols:
        return False, f"Hallucinated columns (not in schema): {sorted(hallucinated_cols)}"

    return True, "Valid"
