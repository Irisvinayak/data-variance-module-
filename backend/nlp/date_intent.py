# date_intent.py — reads what a natural-language query SAYS about dates, as
# structured data, without touching the database.
#
# Two consumers share it so the two NLP endpoints cannot disagree about what a
# phrase means:
#   - date_resolver.py (/variance/nlresolve) maps the intent onto the dates
#     that actually exist in the table.
#   - sql_generator.py (/variance/nlquery) turns it into the RESOLVED TIME
#     CONTEXT block of the SQL prompt.
#
# Every explicit mention becomes a PeriodRef — a date RANGE plus its
# granularity — rather than a single datetime. "March 2025" is the whole month,
# not 01-Mar-2025; treating it as the 1st and then taking "nearest on or before"
# is how "March 2025" used to resolve to 28-Feb-2025.
#
# The fiscal year is a parameter, not a constant: 5.5 (RBI) uses Apr-Mar,
# 6.0 (QCB) the calendar year. See HostProfile.fiscal_year_start_month.

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta

from .query_normalizer import normalize_query

_YEAR_MIN, _YEAR_MAX = 2000, 2035

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Whole month names or their abbreviations only — a bare "mar[a-z]*" would
# read "market 2025" as March 2025.
_MON = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?"
)

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a": 1, "an": 1, "couple of": 2, "few": 3,
}
_N = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"

# Units a relative phrase can step by, in calendar months (days for day/week).
UNIT_MONTHS = {"month": 1, "quarter": 3, "half": 6, "year": 12}
UNIT_DAYS = {"day": 1, "week": 7}


@dataclass(frozen=True)
class PeriodRef:
    start: date
    end: date
    granularity: str          # day | month | quarter | half | year
    text: str

    def label(self) -> str:
        if self.granularity == "day":
            return self.start.strftime("%d-%b-%Y")
        return f"{self.text} ({self.start:%d-%b-%Y} to {self.end:%d-%b-%Y})"


@dataclass
class DateIntent:
    anchor: Optional[PeriodRef] = None          # the "current" period asked for
    targets: list[PeriodRef] = field(default_factory=list)  # explicit comparisons
    relative: Optional[tuple[int, str]] = None  # (n, unit) — "last 3 quarters"
    xox: Optional[str] = None                   # unit of QoQ / MoM / YoY ...
    since: Optional[PeriodRef] = None
    range: Optional[tuple[PeriodRef, PeriodRef]] = None
    this_unit: Optional[str] = None             # "this quarter" / "current FY"
    latest: bool = False                        # "latest", "current", "today"
    spans: list[str] = field(default_factory=list)  # matched text, for display

    @property
    def is_empty(self) -> bool:
        return not (self.anchor or self.targets or self.relative or self.xox
                    or self.since or self.range or self.this_unit)


# ── Calendar helpers ──────────────────────────────────────────────────────────

def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + n
    return idx // 12, idx % 12 + 1


def _span(y: int, m: int, months: int) -> tuple[date, date]:
    ey, em = _add_months(y, m, months - 1)
    return date(y, m, 1), month_end(ey, em)


def _year4(raw: str) -> Optional[int]:
    y = int(raw)
    if len(raw) <= 2:
        y += 2000
    return y if _YEAR_MIN <= y <= _YEAR_MAX else None


def fiscal_year(fy_end_label: int, fy_start: int) -> tuple[int, int]:
    """(start year, start month) of fiscal year `fy_end_label`.

    A fiscal year that does not start in January is named after the calendar
    year it ENDS in (Indian convention: FY25 = Apr-2024..Mar-2025). A
    January-start fiscal year is just the calendar year."""
    return (fy_end_label, 1) if fy_start == 1 else (fy_end_label - 1, fy_start)


def fiscal_year_of(d: date, fy_start: int) -> int:
    """The label of the fiscal year containing `d`."""
    if fy_start == 1:
        return d.year
    return d.year + 1 if d.month >= fy_start else d.year


def period_containing(d: date, unit: str, fy_start: int = 1) -> tuple[date, date]:
    """The calendar/fiscal period of `unit` that contains `d`. Quarters and
    halves are aligned to the fiscal year start, so "this quarter" in an
    Apr-Mar year is Jul-Sep for August, same as the calendar quarter."""
    if unit == "day":
        return d, d
    if unit == "week":
        mon = d - timedelta(days=d.weekday())
        return mon, mon + timedelta(days=6)
    if unit == "month":
        return date(d.year, d.month, 1), month_end(d.year, d.month)
    months = UNIT_MONTHS[unit]
    sy, sm = fiscal_year(fiscal_year_of(d, fy_start), fy_start)
    offset = ((d.year * 12 + d.month - 1) - (sy * 12 + sm - 1)) // months * months
    y, m = _add_months(sy, sm, offset)
    return _span(y, m, months)


# ── Patterns ──────────────────────────────────────────────────────────────────
# Each returns a PeriodRef builder; applied in order and each consumes its span,
# so "Q1 FY25" is claimed before the bare "FY25" pattern sees it, and
# "31-Mar-2025" before "Mar 2025".

_FY = r"(?:fy|f\.y\.|financial\s+year|fiscal\s+year)\s*'?"
_FY_NUM = r"(\d{4}|\d{2})(?:\s*[-/]\s*(\d{4}|\d{2}))?"
_Q = r"q([1-4])(?!\d)"
_H = r"h([12])(?!\d)"
_GAP = r"(?:\s|,|-)*(?:of\s+|for\s+|in\s+)?(?:the\s+)?"
_CY = r"(?:cy|calendar\s+year)\s*'?"
_YEAR = r"((?:19|20)\d{2})"


def _fy_label(first: str, second: Optional[str]) -> Optional[int]:
    """'25' -> 2025; '2024-25' / '24-25' -> 2025 (a range names the end year)."""
    return _year4(second) if second else _year4(first)


def _fy_ref(label: Optional[int], fy_start: int, text: str) -> Optional[PeriodRef]:
    if label is None:
        return None
    sy, sm = fiscal_year(label, fy_start)
    s, e = _span(sy, sm, 12)
    return PeriodRef(s, e, "year", text)


def _fy_part_ref(label: Optional[int], part: int, months: int, fy_start: int,
                 text: str) -> Optional[PeriodRef]:
    if label is None:
        return None
    sy, sm = fiscal_year(label, fy_start)
    y, m = _add_months(sy, sm, (part - 1) * months)
    s, e = _span(y, m, months)
    return PeriodRef(s, e, "quarter" if months == 3 else "half", text)


def _cal_part_ref(year: Optional[int], part: int, months: int, text: str) -> Optional[PeriodRef]:
    if year is None:
        return None
    s, e = _span(year, 1 + (part - 1) * months, months)
    return PeriodRef(s, e, "quarter" if months == 3 else "half", text)


def _day_ref(y: Optional[int], m: int, d: int, text: str) -> Optional[PeriodRef]:
    if y is None or not 1 <= m <= 12:
        return None
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    return PeriodRef(dt, dt, "day", text)


def _month_ref(y: Optional[int], m: int, text: str) -> Optional[PeriodRef]:
    if y is None or not 1 <= m <= 12:
        return None
    s, e = _span(y, m, 1)
    return PeriodRef(s, e, "month", text)


def _numeric_day(a: str, b: str, c: str, text: str) -> Optional[PeriodRef]:
    """d/m/y, falling back to m/d/y only when the day-first reading is
    impossible (second number > 12). Both hosts write dates day-first."""
    y = _year4(c)
    da, db = int(a), int(b)
    return _day_ref(y, db, da, text) or _day_ref(y, da, db, text)


_PATTERNS = [
    # ── fiscal quarter / half ─────────────────────────────────────────────
    (re.compile(rf"\b{_Q}{_GAP}{_FY}{_FY_NUM}\b", re.I),
     lambda m, fy: _fy_part_ref(_fy_label(m[2], m[3]), int(m[1]), 3, fy, m[0])),
    (re.compile(rf"\b{_FY}{_FY_NUM}{_GAP}{_Q}", re.I),
     lambda m, fy: _fy_part_ref(_fy_label(m[1], m[2]), int(m[3]), 3, fy, m[0])),
    (re.compile(rf"\b{_H}{_GAP}{_FY}{_FY_NUM}\b", re.I),
     lambda m, fy: _fy_part_ref(_fy_label(m[2], m[3]), int(m[1]), 6, fy, m[0])),
    (re.compile(rf"\b{_FY}{_FY_NUM}{_GAP}{_H}", re.I),
     lambda m, fy: _fy_part_ref(_fy_label(m[1], m[2]), int(m[3]), 6, fy, m[0])),
    # ── calendar quarter / half ───────────────────────────────────────────
    (re.compile(rf"\b{_Q}{_GAP}(?:{_CY})?{_YEAR}\b", re.I),
     lambda m, fy: _cal_part_ref(_year4(m[2]), int(m[1]), 3, m[0])),
    (re.compile(rf"\b(?:{_CY})?{_YEAR}{_GAP}{_Q}", re.I),
     lambda m, fy: _cal_part_ref(_year4(m[1]), int(m[2]), 3, m[0])),
    (re.compile(rf"\b{_Q}\s*'(\d{{2}})\b", re.I),                       # Q1'25
     lambda m, fy: _cal_part_ref(_year4(m[2]), int(m[1]), 3, m[0])),
    (re.compile(rf"\b{_H}{_GAP}(?:{_CY})?{_YEAR}\b", re.I),
     lambda m, fy: _cal_part_ref(_year4(m[2]), int(m[1]), 6, m[0])),
    (re.compile(rf"\b(first|second)\s+half\s+(?:of\s+)?{_YEAR}\b", re.I),
     lambda m, fy: _cal_part_ref(_year4(m[2]), 1 if m[1].lower() == "first" else 2, 6, m[0])),
    # ── whole fiscal / calendar year ──────────────────────────────────────
    (re.compile(rf"\b{_FY}{_FY_NUM}\b", re.I),
     lambda m, fy: _fy_ref(_fy_label(m[1], m[2]), fy, m[0])),
    (re.compile(rf"\b{_CY}{_YEAR}\b", re.I),
     lambda m, fy: _fy_ref(_year4(m[1]), 1, m[0])),
    # ── explicit days ─────────────────────────────────────────────────────
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),                 # 2025-03-31
     lambda m, fy: _day_ref(_year4(m[1]), int(m[2]), int(m[3]), m[0])),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]*(?:of\s+)?{_MON}[\s\-/.,]*'?(\d{{4}}|\d{{2}})\b", re.I),
     lambda m, fy: _day_ref(_year4(m[3]), _MONTHS[m[2].lower()[:3]], int(m[1]), m[0])),
    (re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),  # March 31, 2025
     lambda m, fy: _day_ref(_year4(m[3]), _MONTHS[m[1].lower()[:3]], int(m[2]), m[0])),
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b"),         # 31/03/2025
     lambda m, fy: _numeric_day(m[1], m[2], m[3], m[0])),
    # ── months ────────────────────────────────────────────────────────────
    (re.compile(rf"\b{_MON}[\s\-/.,]*'?(\d{{4}}|\d{{2}})\b", re.I),        # Mar 2025, Mar-25
     lambda m, fy: _month_ref(_year4(m[2]), _MONTHS[m[1].lower()[:3]], m[0])),
    (re.compile(r"\b(\d{1,2})[/\-](\d{4})\b"),                              # 03/2025
     lambda m, fy: _month_ref(_year4(m[2]), int(m[1]), m[0])),
    # ── bare year (calendar) — last, so richer forms claim the year first ─
    (re.compile(rf"(?<![\d\-/.]){_YEAR}(?![\d\-/.])"),
     lambda m, fy: _fy_ref(_year4(m[1]), 1, m[0])),
]

# Quarter / half with no year ("Q3", "H1"): read against the fiscal year of
# `today` — the most recent such period that has already started.
_BARE_Q_RE = re.compile(r"\b(?:q([1-4])|h([12]))(?![\d\w])", re.I)

_UNIT_WORDS = (
    r"(days?|weeks?|months?|mos?|quarters?|qtrs?|half[\s-]?years?|halves|"
    r"years?|yrs?|financial\s+years?|fiscal\s+years?|fys?|periods?|"
    r"reporting\s+periods?|submissions?|filings?)"
)
_RELATIVE_RE = re.compile(
    rf"\b(?:last|previous|prior|past|preceding|trailing)\s+"
    rf"(?:{_N}\s+|(couple\s+of|few)\s+)?{_UNIT_WORDS}\b",
    re.I,
)
_N_AGO_RE = re.compile(rf"\b{_N}\s+{_UNIT_WORDS}\s+(?:ago|back|before)\b", re.I)
_THIS_RE = re.compile(
    r"\b(?:this|current|present)\s+(month|quarter|half[\s-]?year|year|"
    r"financial\s+year|fiscal\s+year|fy|week)\b", re.I,
)
_XOX = [
    (re.compile(r"\byoy\b|year[\s-]?(?:over|on)[\s-]?year|"
                r"same\s+(?:period|quarter|month|day|date|time)\s+(?:last|previous|prior)\s+year|"
                r"(?:vs\.?|versus|against|compared?\s+(?:to|with))\s+(?:the\s+)?(?:same\s+\w+\s+)?"
                r"(?:a\s+)?year\s+(?:ago|back|before)", re.I), "year"),
    (re.compile(r"\bqoq\b|quarter[\s-]?(?:over|on)[\s-]?quarter", re.I), "quarter"),
    (re.compile(r"\bhoh\b|half[\s-]?(?:over|on)[\s-]?half", re.I), "half"),
    (re.compile(r"\bmom\b|month[\s-]?(?:over|on)[\s-]?month", re.I), "month"),
    (re.compile(r"\bwow\b|week[\s-]?(?:over|on)[\s-]?week", re.I), "week"),
    (re.compile(r"\bdod\b|day[\s-]?(?:over|on)[\s-]?day", re.I), "day"),
]
_LATEST_RE = re.compile(
    r"\b(?:latest|most\s+recent|newest|current|today|as\s+of\s+now|as\s+on\s+date)\b", re.I,
)
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.I)


def _unit(word: str) -> str:
    w = word.lower()
    if w.startswith(("day",)):
        return "day"
    if w.startswith("week"):
        return "week"
    if w.startswith(("month", "mo")):
        return "month"
    if w.startswith(("quarter", "qtr")):
        return "quarter"
    if w.startswith(("half", "halves")):
        return "half"
    if w.startswith(("year", "yr", "financial", "fiscal", "fy")):
        return "year"
    return "period"


def _n(token: Optional[str]) -> int:
    if not token:
        return 1
    t = re.sub(r"\s+", " ", token.strip().lower())
    return int(t) if t.isdigit() else _WORD_NUM.get(t, 1)


def parse_date_intent(query: str, fiscal_start_month: int = 4,
                      today: Optional[date] = None) -> DateIntent:
    """Everything `query` states about dates. `today` anchors phrases with no
    year ("Q3", "this quarter"); callers pass the table's latest data date so
    those read against the data, not the wall clock."""
    today = today or date.today()
    fy = fiscal_start_month if 1 <= fiscal_start_month <= 12 else 4
    text = normalize_query(query or "")
    intent = DateIntent()

    # Explicit periods, with their position so connectors can be read around them.
    found: list[tuple[int, int, PeriodRef]] = []
    rest = text
    for pattern, build in _PATTERNS:
        # `build` bound as a default: sub() calls this synchronously, but binding
        # keeps it correct if the closure ever outlives the iteration.
        def _claim(m: "re.Match[str]", build=build) -> str:
            ref = build(m, fy)
            if ref is None:
                return m.group(0)
            found.append((m.start(), m.end(), ref))
            return " " * len(m.group(0))       # keep offsets stable
        rest = pattern.sub(_claim, rest)

    for m in _BARE_Q_RE.finditer(rest):
        part, months = (int(m[1]), 3) if m[1] else (int(m[2]), 6)
        label = fiscal_year_of(today, fy)
        ref = _fy_part_ref(label, part, months, fy, m[0])
        if ref and ref.start > today:
            ref = _fy_part_ref(label - 1, part, months, fy, m[0])
        if ref:
            found.append((m.start(), m.end(), ref))
    found.sort(key=lambda t: t[0])

    # Relative / XoX / "this" / latest are read from the text left over after
    # the explicit periods were removed, so "last 2 quarters" can never have
    # its "2" read as a date and "Q1FY25" never as "last quarter".
    for m in _RELATIVE_RE.finditer(rest):
        intent.relative = (max(_n(m[1] or m[2]), 1), _unit(m[3]))
        intent.spans.append(m[0])
        break
    if intent.relative is None:
        m = _N_AGO_RE.search(rest)
        if m:
            intent.relative = (max(_n(m[1]), 1), _unit(m[2]))
            intent.spans.append(m[0])
    for pattern, unit in _XOX:
        m = pattern.search(rest)
        if m:
            intent.xox = unit
            intent.spans.append(m[0])
            break
    m = _THIS_RE.search(rest)
    if m:
        intent.this_unit = _unit(m[1].replace("fy", "year"))
        intent.spans.append(m[0])
    if _YESTERDAY_RE.search(rest) and not intent.relative:
        intent.relative = (1, "day")
    if _LATEST_RE.search(rest):
        intent.latest = True

    # How the explicit periods relate: "since X", "between X and Y",
    # "from X to Y", otherwise the newest is the anchor and the rest are
    # comparison targets ("Mar 2025 vs Mar 2024").
    refs = list(found)
    for start, end, ref in list(refs):
        before = text[max(0, start - 12):start].lower()
        if re.search(r"\b(?:since|after|starting|from)\s*$", before) and not intent.since:
            # "from X to Y" is a range, handled below — only a lone "from" is "since".
            later = [r for r in refs if r[0] > end]
            if before.rstrip().endswith("from") and later and re.search(
                r"^\s*(?:to|till|until|through|thru|-)\s*$", text[end:later[0][0]].lower()
            ):
                continue
            intent.since = ref
            refs.remove((start, end, ref))
    for i in range(len(refs) - 1):
        (s1, e1, r1), (s2, _e2, r2) = refs[i], refs[i + 1]
        between = text[e1:s2].lower().strip()
        lead = text[max(0, s1 - 10):s1].lower()
        if (between in ("to", "till", "until", "through", "thru", "-") and re.search(r"from\s*$", lead)) \
                or (between == "and" and re.search(r"between\s*$", lead)):
            lo, hi = sorted((r1, r2), key=lambda r: r.end)
            intent.range = (lo, hi)
            refs = [r for r in refs if r[2] not in (r1, r2)]
            break

    ordered = sorted((r[2] for r in refs), key=lambda r: r.end, reverse=True)
    if ordered:
        intent.anchor = ordered[0]
        seen = {ordered[0]}
        for r in ordered[1:]:
            if r not in seen and (r.start, r.end) != (ordered[0].start, ordered[0].end):
                intent.targets.append(r)
                seen.add(r)
    intent.spans[:0] = [r[2].text for r in found]
    return intent


def step_back(d: date, unit: str, k: int) -> date:
    """`d` moved back k units. Month-end dates stay month-end (31-Mar minus a
    quarter is 31-Dec, not 30-Dec), which is what period-end reporting dates need."""
    if unit in UNIT_DAYS:
        return d - timedelta(days=UNIT_DAYS[unit] * k)
    stepped = d - relativedelta(months=UNIT_MONTHS.get(unit, 1) * k)
    if d == month_end(d.year, d.month):
        stepped = month_end(stepped.year, stepped.month)
    return stepped
