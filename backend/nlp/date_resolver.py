# date_resolver.py — turns date/period intent in an NL query into concrete
# reporting dates ready for the existing, unmodified service.compute_variance().
# Lets /variance/nlresolve go one-shot: no date mentioned -> the latest actual
# submission vs the one before; a date/period IS mentioned -> resolved against
# the dates the table really has, never a calendar guess that might not exist.
#
# What the query SAYS is read by date_intent.parse_date_intent (shared with
# /variance/nlquery); this module only decides which real dates that maps to.

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from ..config import ANONYMOUS, RequestContext
from ..data.db import execute_query
from ..data.service import resolve_physical_table_name
from .date_intent import PeriodRef, parse_date_intent, period_containing, step_back

logger = logging.getLogger(__name__)

_DATE_FMT = "%d-%b-%Y"
_MAX_DATE_SCAN = 500


def _available_dates_desc(
    return_id: str, table_name: str, filter_col: str,
    ctx: RequestContext = ANONYMOUS,
) -> list[datetime]:
    """Every distinct reporting date in the table, newest first, unfiltered.
    Fallback for when service.get_available_dates cannot resolve the table."""
    resolved = resolve_physical_table_name(return_id, table_name, ctx=ctx)
    sql = (
        f"SELECT DISTINCT {filter_col} FROM {resolved} "
        f"ORDER BY {filter_col} DESC FETCH FIRST {_MAX_DATE_SCAN} ROWS ONLY"
    )
    _cols, rows, err = execute_query(sql)
    if err:
        # Older Oracle without FETCH FIRST — same fallback service.py uses.
        sql = (
            f"SELECT DISTINCT {filter_col} FROM "
            f"(SELECT {filter_col} FROM {resolved} ORDER BY {filter_col} DESC) "
            f"WHERE ROWNUM <= {_MAX_DATE_SCAN}"
        )
        _cols, rows, err = execute_query(sql)
        if err:
            logger.warning(
                "[nlp.date_resolver] Could not list dates for %s: %s", resolved, err,
            )
            return []

    out: list[datetime] = []
    for r in rows or []:
        v = r[0]
        if v is None:
            continue
        out.append(v if isinstance(v, datetime)
                   else datetime.combine(v, datetime.min.time()))
    out.sort(reverse=True)
    return out


# ── Frequency -> the period a reporting date closes ──────────────────────────
# Mirrors calculate_variance.validate_reporting_date: H/A close fiscal (Apr-Mar)
# periods, C/B calendar ones. Used to snap "30-Mar-2025" onto 31-Mar-2025 for a
# quarterly table instead of falling a whole quarter back to 31-Dec-2024.
_FREQ_PERIOD = {
    "M": ("month", 1), "MONTHLY": ("month", 1),
    "Q": ("quarter", 1), "QUARTERLY": ("quarter", 1),
    "H": ("half", 4), "HALFYEARLY": ("half", 4), "HY": ("half", 4), "FH": ("half", 4),
    "C": ("half", 1), "CH": ("half", 1),
    "A": ("year", 4), "ANNUAL": ("year", 4), "Y": ("year", 4), "FY": ("year", 4),
    "B": ("year", 1), "CY": ("year", 1),
    "W": ("week", 1), "WEEKLY": ("week", 1),
    "D": ("day", 1), "DAILY": ("day", 1), "G": ("day", 1),
}

# Same ceiling the manual /variance/compute route enforces (MAX_COMPARISON_DATES).
# It caps the WHOLE list, anchor included, so at most two older dates — emitting
# more would build a request the manual route rejects outright.
_MAX_COMPARISON_DATES = 3
_MAX_OLDER_DATES = _MAX_COMPARISON_DATES - 1


def _fmt(d: date) -> str:
    return d.strftime(_DATE_FMT).upper()


def _candidate_dates(
    return_id: str, table_name: str, filter_col: str, ctx: RequestContext,
) -> list[date]:
    """The table's reporting dates, newest first — the same list the manual
    date dropdown shows (service.get_available_dates), so NLP and the wizard
    agree on which dates exist. That list is filtered to dates canonical for
    the return's own frequency, which stops a table shared by a Quarterly and
    an Annual return from handing the Annual one a quarter-end to compare with."""
    try:
        from ..data.service import get_available_dates
        raw = get_available_dates(return_id, table_name, execute_query, ctx=ctx)
        out = sorted({datetime.strptime(v, _DATE_FMT).date() for v in raw}, reverse=True)
        if out:
            return out
    except Exception as exc:  # table-mapping quirks: fall back to the plain scan
        logger.info(
            "[nlp.date_resolver] get_available_dates failed (%s) — scanning %s directly",
            exc, table_name,
        )
    return sorted(
        {d.date() for d in _available_dates_desc(return_id, table_name, filter_col, ctx)},
        reverse=True,
    )


class _Picker:
    """Maps PeriodRefs and calendar steps onto dates that exist, recording a
    note every time it had to substitute, so the answer is never silently
    about a different date than the one asked for."""

    def __init__(self, cands: list[date], freq: str):
        self.cands = cands
        self.freq = freq
        self.notes: list[str] = []

    def ref(self, ref: PeriodRef) -> date:
        inside = [c for c in self.cands if ref.start <= c <= ref.end]
        if inside:
            return inside[0]
        what = ref.text.strip()
        if ref.granularity == "day" and self.freq in _FREQ_PERIOD:
            unit, fy = _FREQ_PERIOD[self.freq]
            _start, end = period_containing(ref.start, unit, fy)
            if end in self.cands:
                self.notes.append(
                    f"{_fmt(ref.start)} is not a reporting date; used the period end {_fmt(end)}."
                )
                return end
        if ref.start > self.cands[0]:
            pick = self.cands[0]
            self.notes.append(f"No data yet for {what}; used the latest available date {_fmt(pick)}.")
            return pick
        before = [c for c in self.cands if c < ref.start]
        if before:
            pick = before[0]
            self.notes.append(f"No data for {what}; used the nearest earlier date {_fmt(pick)}.")
            return pick
        pick = self.cands[-1]
        self.notes.append(f"{what} is before the earliest data; used the earliest date {_fmt(pick)}.")
        return pick

    def on_or_before(self, target: date, what: str) -> Optional[date]:
        hits = [c for c in self.cands if c <= target]
        if not hits:
            return None
        if hits[0] != target:
            self.notes.append(f"No data for {what} ({_fmt(target)}); used {_fmt(hits[0])}.")
        return hits[0]

    def steps(self, anchor: date, n: int, unit: str) -> list[date]:
        """The dates 1..n calendar units back from `anchor`. The unit 'period'
        means the previous n submissions, whatever their spacing."""
        older = [c for c in self.cands if c < anchor]
        if unit == "period":
            if len(older) < n:
                self.notes.append(f"Asked for {n} earlier period(s); only {len(older)} exist.")
            return older[:n]
        out: list[date] = []
        for k in range(1, n + 1):
            plural = "s" if k > 1 else ""
            hit = self.on_or_before(step_back(anchor, unit, k), f"{k} {unit}{plural} back")
            if hit and hit < anchor and hit not in out:
                out.append(hit)
        return out


def resolve_reporting_date(
    query: str, return_id: str, table_name: str, filter_col: str, report_freq: str,
    ctx: RequestContext = ANONYMOUS,
) -> tuple[str, int, list[str], list[str]]:
    """(reporting_date, reporting_period, comparison_dates, notes).

    comparison_dates are REAL dates from the table, newest first and including
    the anchor, exactly as the manual UI supplies them. They are chosen by what
    the query MEANS (a named date, the same quarter a year ago, three quarters
    back), not by "the next rows down". `notes` says, in words a user can read,
    every place the answer had to differ from the literal request.

    Never fails on a date phrase: anything unmatched falls back to a real date
    with a note. The only error is a table with no data at all (ValueError).
    comparison_dates always holds at least the anchor.
    """
    from ..hosts import get_profile

    freq = (report_freq or "M").strip().upper() or "M"
    fy_start = get_profile().fiscal_year_start_month

    cands = _candidate_dates(return_id, table_name, filter_col, ctx)
    if not cands:
        raise ValueError(f"No data found in {table_name} to determine a reporting date.")
    latest = cands[0]

    # Phrases with no year ("Q3", "this quarter") read against the data's
    # newest date, not the wall clock — the data can lag the calendar by months.
    intent = parse_date_intent(query, fy_start, today=latest)
    pick = _Picker(cands, freq)

    # ── the anchor (the "current" period) ────────────────────────────────
    if intent.range:
        anchor = pick.ref(intent.range[1])
    elif intent.anchor:
        anchor = pick.ref(intent.anchor)
    else:
        anchor = latest

    # ── what to compare it with, most specific statement first ───────────
    if intent.targets:
        comps = [pick.ref(t) for t in intent.targets]
        requested = len(intent.targets)
    elif intent.range or intent.since:
        start = (intent.range[0] if intent.range else intent.since).start
        comps = [c for c in cands if start <= c < anchor]
        if not comps:
            comps = [c for c in cands if c < anchor][:1]
            if comps:
                pick.notes.append(
                    f"No data between {_fmt(start)} and {_fmt(anchor)}; compared with {_fmt(comps[0])}."
                )
        requested = max(len(comps), 1)
    elif intent.xox:
        comps = pick.steps(anchor, 1, intent.xox)
        requested = 1
    elif intent.relative:
        n, unit = intent.relative
        comps = pick.steps(anchor, n, unit)
        requested = n
    else:
        comps = [c for c in cands if c < anchor][:1]
        requested = 1

    comps = sorted({c for c in comps if c != anchor}, reverse=True)
    if len(comps) > _MAX_OLDER_DATES:
        # Keep the nearest and the furthest asked for: that spans the whole
        # requested window within the limit.
        pick.notes.append(
            f"Showing {_MAX_COMPARISON_DATES} of {len(comps) + 1} periods "
            f"(limit {_MAX_COMPARISON_DATES}): {_fmt(comps[0])} and {_fmt(comps[-1])}."
        )
        comps = [comps[0], comps[-1]]
    if not comps and anchor == cands[-1]:
        pick.notes.append(
            f"{_fmt(anchor)} is the earliest date in the table; there is no earlier period to compare with."
        )

    anchor_str = _fmt(anchor)
    # Always explicit, even with nothing older: an empty list would send
    # compute_variance to RepFreq arithmetic, whose frequency check can reject
    # a real but off-cycle date — the query must still execute.
    comparison = [anchor_str] + [_fmt(c) for c in comps]
    logger.info(
        "[nlp.date_resolver] query=%r | fy_start=%d freq=%s | intent=%s | anchor=%s | "
        "comparison_dates=%s | notes=%s",
        query, fy_start, freq, intent.spans, anchor_str, comparison[1:], pick.notes,
    )
    return anchor_str, max(requested, 1), comparison, pick.notes
