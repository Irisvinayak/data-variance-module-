# date_resolver.py — turns date/period intent in an NL query into a concrete
# (reporting_date, reporting_period) pair ready for the existing, unmodified
# service.compute_variance(). Lets /variance/nlresolve go one-shot: no date
# mentioned -> use the latest actual submission; a date/period IS mentioned ->
# resolve it against real data instead of guessing a calendar date that might
# not exist.

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional, Tuple

from dateutil import parser as dateutil_parser

from ..calculate_variance import get_previous_dates
from ..config import DP_TABLE_SCHEMA, IS_SP_TABLE_DATA_ENABLED
from ..db import execute_query
from ..report_lookup import _parse_returns, get_is_excel_by_return_code

logger = logging.getLogger(__name__)

_DATE_FMT = "%d-%b-%Y"
_MAX_PERIOD_WALK = 12  # safety cap when counting steps between two explicit periods


def _resolve_physical_table_name(return_id: str, table_name: str) -> str:
    """Mirrors service.compute_variance()'s inline table-name resolution
    (is_excel -> optional _DP suffix -> optional DP_TABLE_SCHEMA prefix).
    Duplicated here deliberately, read-only use only (a MAX() lookup) —
    service._resolve_report_table_name() is dead/incomplete code (it's
    missing the DP_TABLE_SCHEMA prefix), so it is not reused."""
    return_meta = next((r for r in _parse_returns() if r.get("Id") == str(return_id)), None)
    is_excel = (
        str(return_meta.get("IsExcel", "false")).strip().lower() == "true"
        if return_meta
        else get_is_excel_by_return_code(return_id)
    )
    if IS_SP_TABLE_DATA_ENABLED and not is_excel:
        dp_name = f"{table_name}_DP"
        return f"{DP_TABLE_SCHEMA}.{dp_name}" if DP_TABLE_SCHEMA else dp_name
    return table_name


def _latest_available_date(return_id: str, table_name: str, filter_col: str) -> Optional[datetime]:
    resolved = _resolve_physical_table_name(return_id, table_name)
    sql = f"SELECT MAX({filter_col}) FROM {resolved}"
    _cols, rows, err = execute_query(sql)
    if err or not rows or rows[0][0] is None:
        return None
    value = rows[0][0]
    return value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())


def _nearest_available_on_or_before(
    return_id: str, table_name: str, filter_col: str, target: datetime
) -> Optional[datetime]:
    resolved = _resolve_physical_table_name(return_id, table_name)
    target_str = target.strftime(_DATE_FMT).upper()
    sql = (
        f"SELECT MAX({filter_col}) FROM {resolved} "
        f"WHERE {filter_col} <= TO_DATE('{target_str}', 'DD-MON-YYYY')"
    )
    _cols, rows, err = execute_query(sql)
    if err or not rows or rows[0][0] is None:
        return None
    value = rows[0][0]
    return value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())


_YEAR_RANGE = range(2000, 2036)

# A bare small number ("last 2 quarters") must NEVER be treated as a date —
# dateutil's fuzzy mode will happily turn a lone "2" into "02-Jan-2026".
# Only attempt a date parse when the text actually looks date-shaped: a
# month name, a 4-digit year, or a DD/MM(/YYYY)-style separated number group.
_MONTH_NAME_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE
)
_YEAR_TOKEN_RE = re.compile(r"\b(19|20)\d{2}\b")
_DATE_SEP_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")


def _looks_date_shaped(text: str) -> bool:
    return bool(_MONTH_NAME_RE.search(text) or _YEAR_TOKEN_RE.search(text) or _DATE_SEP_RE.search(text))


def _try_parse_date(text: str) -> Optional[datetime]:
    """Best-effort explicit date parse — only attempted when the substring
    looks date-shaped (see _looks_date_shaped), and only trusted when the
    resulting year is plausible for this application's data."""
    if not _looks_date_shaped(text):
        return None
    try:
        parsed = dateutil_parser.parse(text, fuzzy=True, default=datetime(date.today().year, 1, 1))
    except (ValueError, OverflowError):
        return None
    if parsed.year not in _YEAR_RANGE:
        return None
    return parsed


_COMPARISON_SPLIT_RE = re.compile(r"\b(?:vs\.?|versus|and|to)\b", re.IGNORECASE)

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _extract_n(token: str) -> int:
    return int(token) if token.isdigit() else _WORD_NUM.get(token.lower(), 0)


_N_WORD_RE = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"


def _extract_relative_periods_back(query: str) -> int:
    """Detects 'last/previous/past N quarters|months|years|periods'. Returns
    0 if nothing found (caller treats that as 'no relative phrase')."""
    m = re.search(
        rf"\b(?:last|previous|past|trailing)\s+{_N_WORD_RE}\s+"
        r"(?:quarters?|months?|years?|periods?|reporting\s+periods?)\b",
        query, re.IGNORECASE,
    )
    if not m:
        return 0
    return max(_extract_n(m.group(1)), 0)


def _extract_two_dates(query: str) -> Optional[Tuple[datetime, datetime]]:
    """Splits the query on comparison connectors (vs/versus/and/to) and tries
    to parse an explicit date/period on each side. Returns (later, earlier)
    if both sides yield a distinct date, else None."""
    parts = _COMPARISON_SPLIT_RE.split(query)
    if len(parts) < 2:
        return None
    candidates = [d for d in (_try_parse_date(p) for p in parts) if d is not None]
    if len(candidates) < 2:
        return None
    a, b = candidates[0], candidates[-1]
    if a.date() == b.date():
        return None
    return (a, b) if a > b else (b, a)


def _steps_between(anchor: datetime, target: datetime, report_freq: str) -> int:
    """Counts how many frequency-steps back from `anchor` land at or before
    `target`, capped at _MAX_PERIOD_WALK."""
    for n in range(1, _MAX_PERIOD_WALK + 1):
        stepped = get_previous_dates(anchor, report_freq, n)[-1]
        if stepped <= target:
            return n
    return _MAX_PERIOD_WALK


def resolve_reporting_date(
    query: str, return_id: str, table_name: str, filter_col: str, report_freq: str,
) -> Tuple[str, int]:
    """Returns (reporting_date, reporting_period) ready for
    service.compute_variance(). Raises ValueError if the table has no data
    at all (nothing to anchor to)."""
    freq = (report_freq or "M").strip().upper() or "M"

    latest = _latest_available_date(return_id, table_name, filter_col)
    if latest is None:
        raise ValueError(
            f"No data found in {table_name} to determine a reporting date."
        )

    # Checked first, before any fuzzy date parsing: "last/previous N
    # quarters/months/years" is an exact, unambiguous regex match — it must
    # win over _try_parse_date ever getting a chance to fuzzy-match the bare
    # numeral (e.g. the "2" in "last 2 quarters") as a bogus calendar date.
    n = _extract_relative_periods_back(query)
    if n > 0:
        logger.info("[nlp.date_resolver] query=%r -> relative %d period(s) back from latest=%s", query, n, latest)
        return latest.strftime(_DATE_FMT).upper(), n

    two_dates = _extract_two_dates(query)
    if two_dates:
        later, earlier = two_dates
        anchor = _nearest_available_on_or_before(return_id, table_name, filter_col, later) or latest
        periods = _steps_between(anchor, earlier, freq)
        logger.info(
            "[nlp.date_resolver] query=%r -> two explicit periods, anchor=%s periods=%d",
            query, anchor, periods,
        )
        return anchor.strftime(_DATE_FMT).upper(), max(periods, 1)

    single_date = _try_parse_date(query)
    if single_date:
        anchor = _nearest_available_on_or_before(return_id, table_name, filter_col, single_date)
        if anchor is None:
            raise ValueError(
                f"No data found in {table_name} on or before {single_date.strftime(_DATE_FMT)}."
            )
        logger.info("[nlp.date_resolver] query=%r -> explicit date, anchor=%s", query, anchor)
        return anchor.strftime(_DATE_FMT).upper(), 1

    logger.info("[nlp.date_resolver] query=%r -> no date/period intent, using latest=%s", query, latest)
    return latest.strftime(_DATE_FMT).upper(), 1
