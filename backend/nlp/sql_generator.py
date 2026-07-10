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

import calendar
import json
import logging
import re
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

import requests

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


def load_samples(path: str = DESCRIPTION_SAMPLES_PATH) -> Dict[str, Dict[str, List[str]]]:
    """Load the full row-label samples dict the external build tool produced
    (backend/output/description_samples.json) — supplements the FAISS top-K
    matches with every known label value for a matched table. Returns {} if
    the file doesn't exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


BANNED_KEYWORDS = ["delete", "update", "drop", "insert", "truncate", "alter", "create", "exec"]
MAX_LABELS_MINIMAL = 8


def _resolve_relative_time(query: str, today: date) -> Optional[str]:
    """Detect relative time expressions ('last quarter', 'last 3 months', ...)
    and resolve them to concrete calendar date ranges, injected into the
    prompt so the LLM never has to guess what a phrase like that means."""
    q = query.lower()
    lines = []

    def fmt(d):
        return d.strftime("%Y-%m-%d")

    def month_end(y, m):
        return date(y, m, calendar.monthrange(y, m)[1])

    from datetime import timedelta

    if re.search(r'\b(this|current)\s+week\b', q):
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        lines.append(f"'this week'  = {fmt(mon)} to {fmt(sun)}")

    if re.search(r'\b(last|previous)\s+week\b', q):
        mon = today - timedelta(days=today.weekday() + 7)
        sun = mon + timedelta(days=6)
        lines.append(f"'last week'  = {fmt(mon)} to {fmt(sun)}")

    if re.search(r'\b(this|current)\s+month\b', q):
        start = today.replace(day=1)
        end = month_end(today.year, today.month)
        lines.append(f"'this month' = {today.strftime('%B %Y')}  ({fmt(start)} to {fmt(end)})")

    if re.search(r'\b(last|previous)\s+month\b', q):
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        lines.append(f"'last month' = {end.strftime('%B %Y')}  ({fmt(start)} to {fmt(end)})")

    def _months_back(y, m, n):
        idx = (y * 12 + (m - 1)) - n
        return idx // 12, idx % 12 + 1

    _WORD_NUM = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }

    def _extract_n(token: str) -> int:
        return int(token) if token.isdigit() else _WORD_NUM.get(token.lower(), 0)

    _N_WORD_RE = r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)'

    m_months = re.search(rf'\b(?:last|previous|past|trailing)\s+{_N_WORD_RE}\s+months?\b', q)
    if m_months:
        n = _extract_n(m_months.group(1))
        if n > 0:
            end_y, end_m = _months_back(today.year, today.month, 1)
            end = month_end(end_y, end_m)
            start_y, start_m = _months_back(today.year, today.month, n)
            start = date(start_y, start_m, 1)
            lines.append(
                f"'last {n} months' = {start.strftime('%B %Y')} to {end.strftime('%B %Y')}  "
                f"({fmt(start)} to {fmt(end)})"
            )

    m_years = re.search(rf'\b(?:last|previous|past|trailing)\s+{_N_WORD_RE}\s+years?\b', q)
    if m_years:
        n = _extract_n(m_years.group(1))
        if n > 0:
            end_y = today.year - 1
            start_y = today.year - n
            lines.append(f"'last {n} years' = {start_y} to {end_y}  (01-JAN-{start_y} to 31-DEC-{end_y})")

    if re.search(r'\b(this|current)\s+year\b', q):
        lines.append(f"'this year'  = {today.year}  (01-JAN-{today.year} to 31-DEC-{today.year})")

    if re.search(r'\b(last|previous)\s+year\b', q):
        y = today.year - 1
        lines.append(f"'last year'  = {y}  (01-JAN-{y} to 31-DEC-{y})")

    _CQ_START = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}
    _CQ_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    cur_cq = (today.month - 1) // 3 + 1

    if re.search(r'\b(this|current)\s+quarter\b', q):
        s = date(today.year, *_CQ_START[cur_cq])
        e = date(today.year, *_CQ_END[cur_cq])
        lines.append(f"'this quarter' = Q{cur_cq} {today.year} (calendar)  ({fmt(s)} to {fmt(e)})")

    if re.search(r'\b(last|previous)\s+quarter\b', q):
        prev_cq = cur_cq - 1 if cur_cq > 1 else 4
        prev_cq_year = today.year if cur_cq > 1 else today.year - 1
        s = date(prev_cq_year, *_CQ_START[prev_cq])
        e = date(prev_cq_year, *_CQ_END[prev_cq])
        lines.append(f"'last quarter' = Q{prev_cq} {prev_cq_year} (calendar)  ({fmt(s)} to {fmt(e)})")

    m_quarters = re.search(rf'\b(?:last|previous|past|trailing)\s+{_N_WORD_RE}\s+quarters?\b', q)
    if m_quarters:
        n = _extract_n(m_quarters.group(1))
        if n > 0:
            end_cq, end_cq_year = cur_cq - 1, today.year
            if end_cq == 0:
                end_cq, end_cq_year = 4, today.year - 1
            start_idx = (end_cq_year * 4 + (end_cq - 1)) - (n - 1)
            start_cq_year, start_cq = start_idx // 4, start_idx % 4 + 1
            s = date(start_cq_year, *_CQ_START[start_cq])
            e = date(end_cq_year, *_CQ_END[end_cq])
            unit = "quarter" if n == 1 else "quarters"
            lines.append(
                f"'last {n} {unit}' = Q{start_cq} {start_cq_year} to Q{end_cq} {end_cq_year} (calendar)  "
                f"({fmt(s)} to {fmt(e)})"
            )

    fy_sy = today.year if today.month >= 4 else today.year - 1
    _FYQ = {
        1: {"months": {4, 5, 6}, "start": (4, 1), "end": (6, 30), "offset": 0},
        2: {"months": {7, 8, 9}, "start": (7, 1), "end": (9, 30), "offset": 0},
        3: {"months": {10, 11, 12}, "start": (10, 1), "end": (12, 31), "offset": 0},
        4: {"months": {1, 2, 3}, "start": (1, 1), "end": (3, 31), "offset": 1},
    }
    cur_fyq = next(fq for fq, v in _FYQ.items() if today.month in v["months"])

    if re.search(r'\b(this|current)\s+(fy|financial|fiscal)\s*(quarter|q)\b', q):
        off = _FYQ[cur_fyq]["offset"]
        s = date(fy_sy + off, *_FYQ[cur_fyq]["start"])
        e = date(fy_sy + off, *_FYQ[cur_fyq]["end"])
        lines.append(f"'this FY quarter' = Q{cur_fyq} FY{fy_sy+1}  ({fmt(s)} to {fmt(e)})")

    if re.search(r'\b(last|previous)\s+(fy|financial|fiscal)\s*(quarter|q)\b', q):
        prev_fyq = cur_fyq - 1 if cur_fyq > 1 else 4
        prev_fy_sy = fy_sy if cur_fyq > 1 else fy_sy - 1
        off = _FYQ[prev_fyq]["offset"]
        s = date(prev_fy_sy + off, *_FYQ[prev_fyq]["start"])
        e = date(prev_fy_sy + off, *_FYQ[prev_fyq]["end"])
        lines.append(f"'last FY quarter' = Q{prev_fyq} FY{prev_fy_sy+1}  ({fmt(s)} to {fmt(e)})")

    if re.search(r'\b(this|current)\s+(financial year|fiscal year|fy)\b(?!\s*(?:quarter|q)\b)', q):
        lines.append(f"'this financial year' = FY{fy_sy+1}  (01-APR-{fy_sy} to 31-MAR-{fy_sy+1})")

    if re.search(r'\b(last|previous)\s+(financial year|fiscal year|fy)\b(?!\s*(?:quarter|q)\b)', q):
        lines.append(f"'last financial year' = FY{fy_sy}  (01-APR-{fy_sy-1} to 31-MAR-{fy_sy})")

    if not lines:
        return None

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
    backend/output/) — the LLM sees every column of a matched table, not
    just the top-K the embedding retrieval happened to surface."""
    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except FileNotFoundError:
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

    label_map: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
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
    _time_block = _resolve_relative_time(user_query, today_obj)
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


def _call_ollama(prompt_text: str, model_name: str, model_profile: dict) -> str:
    options = {}
    if model_profile.get("temperature") is not None:
        options["temperature"] = model_profile["temperature"]
    if model_profile.get("num_predict") is not None:
        options["num_predict"] = model_profile["num_predict"]

    payload = {"model": model_name, "prompt": prompt_text, "stream": False}
    if options:
        payload["options"] = options

    logger.info("[nlp.sql_generator] Calling Ollama | model=%s | prompt_chars=%d", model_name, len(prompt_text))
    try:
        response = _session.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT_SEC)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error("[nlp.sql_generator] Ollama call failed | model=%s | %s", model_name, exc)
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    text = response.json().get("response", "")
    logger.info("[nlp.sql_generator] Ollama responded | model=%s | response_chars=%d", model_name, len(text))
    return text


def generate_sql(user_query, tables, columns, dialect="Oracle", today_date=None, matched_labels=None) -> Dict[str, Any]:
    """Generate + validate a SQL SELECT for `user_query`, grounded strictly in
    `tables`/`columns` (the caller's already-authorized shortlist). Returns
    {"sql": str, "warnings": [str, ...]} — non-empty warnings means the SQL
    failed validation even after one retry and should NOT be executed."""
    prompt = build_prompt(user_query, tables, columns, dialect=dialect, today_date=today_date, matched_labels=matched_labels)
    model_name = OLLAMA_MODEL
    model_profile = _get_model_profile(model_name)

    try:
        raw = _call_ollama(prompt, model_name, model_profile)
    except RuntimeError as exc:
        logger.error("[nlp.sql_generator] %s", exc)
        return {"sql": "", "warnings": [str(exc)]}

    raw = re.sub(r'^```(?:sql)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r'```\s*$', '', raw).strip()
    raw = raw.rstrip().rstrip(";")

    is_valid, reason = validate_sql(raw, tables, columns)
    warnings: List[str] = []

    if not is_valid:
        retry_prompt = (
            "The previous SQL was invalid. Return ONLY the corrected SQL.\n\n"
            f"Original prompt:\n{prompt}\n\n"
            f"Invalid SQL:\n{raw}\n\n"
            f"Validation reason:\n{reason}\n\n"
            "Corrected SQL:"
        )
        try:
            raw = _call_ollama(retry_prompt, model_name, model_profile)
        except RuntimeError as exc:
            logger.error("[nlp.sql_generator] Retry failed: %s", exc)
            return {"sql": "", "warnings": [str(exc)]}
        raw = re.sub(r'^```(?:sql)?\s*', '', raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r'```\s*$', '', raw).strip()
        raw = raw.rstrip().rstrip(";")
        is_valid, reason = validate_sql(raw, tables, columns)

    if not is_valid:
        category = _validation_failure_category(reason)
        warning = f"Model '{model_name}' generated invalid SQL; probable failure category: {category}. Reason: {reason}"
        warnings.append(warning)
        logger.warning("[nlp.sql_generator] %s", warning)
        return {"sql": "", "warnings": warnings}

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

    for word in BANNED_KEYWORDS:
        if re.search(rf'\b{word}\b', q):
            return False, f"Dangerous keyword detected: '{word}'"

    valid_table_names = {t["table"].lower() for t in tables}
    all_columns = _load_all_columns(valid_table_names)
    valid_col_names = {c["column"].lower() for c in all_columns}

    subquery_aliases = set(re.findall(r'\bas\s+([a-z_][a-z0-9_]*)', q))
    subquery_aliases |= set(re.findall(r'\)\s+([a-z_][a-z0-9_]*)\b', q))

    q_for_tables = re.sub(r'\bextract\s*\([^)]*\)', '', q)
    q_for_tables = re.sub(r'\btrim\s*\([^)]*\)', '', q_for_tables)
    referenced_tables = set(re.findall(r'(?:from|join)\s+([a-z_][a-z0-9_]*)', q_for_tables))
    real_table_refs = referenced_tables - subquery_aliases
    hallucinated_tables = real_table_refs - valid_table_names
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
