# date_resolver.py — turns date/period intent in an NL query into a concrete
# (reporting_date, reporting_period) pair ready for the existing, unmodified
# service.compute_variance(). Lets /variance/nlresolve go one-shot: no date
# mentioned -> use the latest actual submission; a date/period IS mentioned ->
# resolve it against real data instead of guessing a calendar date that might
# not exist.

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime
from typing import Optional, Tuple

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

from ..calculate_variance import get_previous_dates
from ..config import DP_TABLE_SCHEMA, IS_SP_TABLE_DATA_ENABLED
from ..db import execute_query
from ..report_lookup import _parse_returns, get_is_excel_by_return_code
from .query_normalizer import normalize_query

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


# "Q1FY25"/"Q1 FY25"/"FY25 Q1"/"FY2024-25 Q1" — Indian financial-year quarter
# notation (Apr-Mar), extremely common phrasing for this app's RBI-regulated
# data but NOT something dateutil's fuzzy parser understands (it would either
# ignore "FY25" entirely for lacking a 4-digit year, or mis-parse it). Checked
# ahead of the generic dateutil path in _try_parse_date for that reason.
# Gap between the Q-token and the FY-token: optional whitespace/comma plus
# an optional connector word ("Q1 of FY25", "Q1, FY25") — deliberately NOT
# a `\b` boundary check on the digit itself: "Q1FY25" has no boundary
# between "1" and "F" (both are \w characters), so `\bQ([1-4])\b` would
# never match the glued form at all. `(?!\d)` blocks "Q12"-style false
# matches instead.
_FY_Q_GAP = r"(?:\s|,)*(?:of\s+|for\s+)?"
_Q_THEN_FY_RE = re.compile(rf"\bQ([1-4])(?!\d){_FY_Q_GAP}FY\s*'?(\d{{2,4}})(-\d{{2,4}})?\b", re.IGNORECASE)
_FY_THEN_Q_RE = re.compile(rf"\bFY\s*'?(\d{{2,4}})(-\d{{2,4}})?{_FY_Q_GAP}Q([1-4])(?!\d)", re.IGNORECASE)


def _fy_quarter_end_date(fy_end_year: int, quarter: int) -> datetime:
    """FYyy runs Apr(yy-1)-Mar(yy). Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec (all in
    the calendar year BEFORE fy_end_year), Q4=Jan-Mar (in fy_end_year itself).
    Returns that quarter's last calendar day."""
    if quarter == 4:
        month, year = 3, fy_end_year
    else:
        month, year = 3 + 3 * quarter, fy_end_year - 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day)


def _try_parse_fy_quarter(text: str) -> Optional[datetime]:
    m = _Q_THEN_FY_RE.search(text)
    if m:
        quarter, fy_raw, fy_range_suffix = int(m.group(1)), m.group(2), m.group(3)
    else:
        m = _FY_THEN_Q_RE.search(text)
        if not m:
            return None
        fy_raw, fy_range_suffix, quarter = m.group(1), m.group(2), int(m.group(3))
    if fy_range_suffix:
        # "FY2024-25" dash-range notation — ambiguous which half the caller
        # means without more context; decline rather than silently guessing
        # the wrong one (e.g. reading "FY2024-25" as ending in 2024).
        return None
    # "FY25"/"FY2025" both denote the fiscal year ENDING in that year (Indian
    # convention) — 2-digit forms are expanded assuming the 2000s.
    fy_end_year = int(fy_raw) if len(fy_raw) == 4 else 2000 + int(fy_raw)
    if fy_end_year not in _YEAR_RANGE:
        return None
    return _fy_quarter_end_date(fy_end_year, quarter)


def _try_parse_date(text: str) -> Optional[datetime]:
    """Best-effort explicit date parse — only attempted when the substring
    looks date-shaped (see _looks_date_shaped), and only trusted when the
    resulting year is plausible for this application's data. FY-quarter
    notation is checked first since it's an unambiguous explicit marker that
    doesn't need (and wouldn't reliably pass) the date-shaped guard below."""
    fy_quarter = _try_parse_fy_quarter(text)
    if fy_quarter is not None:
        return fy_quarter
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
    """Detects 'last/previous/past N quarters|months|years|periods'. The count
    is OPTIONAL — bare "last quarter"/"previous month" (no number) is common
    phrasing and means N=1, same as this function's own no-count default.
    Unit words also accept the abbreviations (qtr/qtrs, mo/mos) that survive
    even after normalize_query()'s typo/abbreviation dictionary has already
    run on `query` (see resolve_reporting_date) — belt-and-suspenders so a
    dictionary miss doesn't silently defeat this match. Returns 0 if nothing
    found at all (caller treats that as 'no relative phrase')."""
    m = re.search(
        rf"\b(?:last|previous|past|trailing)\s+(?:{_N_WORD_RE}\s+)?"
        r"(?:quarters?|qtrs?|months?|mos?|years?|yrs?|periods?|reporting\s+periods?)\b",
        query, re.IGNORECASE,
    )
    if not m:
        return 0
    if m.group(1) is None:
        return 1
    return max(_extract_n(m.group(1)), 0)


# Finance-shorthand period-over-period phrasing (QoQ/MoM/YoY/WoW, and their
# spelled-out equivalents). YoY specifically means "the same period one
# calendar YEAR ago", which is freq-dependent (4 periods back for quarterly
# data, 12 for monthly, ...) — resolved via _steps_between, same mechanism
# the two-explicit-dates path already uses. QoQ/MoM/WoW all mean "compare
# with the immediately preceding reporting period" (N=1) — the table's own
# report_freq already defines what one period is, so no freq-specific
# handling is needed for those.
_YOY_TOKEN_RE = re.compile(
    r"\byoy\b|year[\s-]?over[\s-]?year|same\s+(?:period|quarter|month)\s+last\s+year",
    re.IGNORECASE,
)
_XOX_RE = re.compile(
    r"\b(?:qoq|mom|yoy|wow)\b|"
    r"quarter[\s-]?over[\s-]?quarter|month[\s-]?over[\s-]?month|"
    r"year[\s-]?over[\s-]?year|week[\s-]?over[\s-]?week|"
    r"same\s+(?:period|quarter|month)\s+last\s+year",
    re.IGNORECASE,
)


def _extract_xox_periods(query: str, anchor: datetime, freq: str) -> int:
    """Returns 0 if no XoX-style phrase is present."""
    if not _XOX_RE.search(query):
        return 0
    if _YOY_TOKEN_RE.search(query):
        one_year_back = anchor - relativedelta(years=1)
        return max(_steps_between(anchor, one_year_back, freq), 1)
    return 1


# "since March 2024" / "since Q1FY24" — open-ended range from an explicit
# start date up to the latest available submission (as opposed to "on
# March 2024", which anchors ON that date with period=1). Reuses
# _try_parse_date (including its FY-quarter support) on whatever follows
# "since".
_SINCE_RE = re.compile(r"\bsince\s+(.+?)$", re.IGNORECASE)


def _extract_since_date(query: str) -> Optional[datetime]:
    m = _SINCE_RE.search(query)
    if not m:
        return None
    return _try_parse_date(m.group(1))


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

    # Fix known typos/abbreviations ("perids"->"periods", "quater"->"quarter",
    # ...) before any regex matching below — a misspelled unit word must not
    # silently defeat period-count detection and fall through to the
    # single-period default. Shares the same dictionary retriever.py already
    # normalizes through for embedding quality (backend/nlp/query_normalizer.py).
    original_query, query = query, normalize_query(query)
    if query != original_query:
        logger.info("[nlp.date_resolver] query normalized: %r -> %r", original_query, query)

    latest = _latest_available_date(return_id, table_name, filter_col)
    if latest is None:
        raise ValueError(
            f"No data found in {table_name} to determine a reporting date."
        )

    # Checked first, before any fuzzy date parsing: finance shorthand
    # (QoQ/MoM/YoY/WoW) and "last/previous N quarters/months/years" are both
    # exact, unambiguous regex matches — they must win over _try_parse_date
    # ever getting a chance to fuzzy-match a bare numeral (e.g. the "2" in
    # "last 2 quarters") as a bogus calendar date.
    xox_n = _extract_xox_periods(query, latest, freq)
    if xox_n > 0:
        logger.info("[nlp.date_resolver] query=%r -> XoX shorthand, %d period(s) back from latest=%s", query, xox_n, latest)
        return latest.strftime(_DATE_FMT).upper(), xox_n

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

    # "since <date>" — open-ended range from an explicit start up to the
    # latest submission, distinct from "on <date>" (which anchors ON that
    # date with period=1, handled below).
    since_date = _extract_since_date(query)
    if since_date:
        periods = _steps_between(latest, since_date, freq)
        logger.info(
            "[nlp.date_resolver] query=%r -> since date=%s, periods=%d",
            query, since_date, periods,
        )
        return latest.strftime(_DATE_FMT).upper(), max(periods, 1)

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
