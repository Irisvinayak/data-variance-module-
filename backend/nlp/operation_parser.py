# operation_parser.py — what does the user want DONE with the variance data?
#
# query_analyzer.py answers "which return / which dates"; this answers the other
# half: compare, filter by direction or threshold, rank (top/bottom N), find
# unchanged or new/removed values, show a trend, or flag anomalies. The result is a
# structured AnalysisSpec that analysis.py applies after the variance is computed.
#
# Rule-based on purpose: the operation is a small closed vocabulary, a regex is
# deterministic and testable, and an LLM must not decide what a "threshold" means in
# a regulatory report.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .number_units import amount_pattern, build_amount

# ── Vocabulary ────────────────────────────────────────────────────────────────
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUM = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"

_TOP_N_RE = re.compile(
    rf"\b(?:top|highest|largest|biggest|maximum|max|most)\s+{_NUM}\b"
    rf"|\b{_NUM}\s+(?:highest|largest|biggest|most)\b", re.I)
_BOTTOM_N_RE = re.compile(
    rf"\b(?:bottom|lowest|smallest|minimum|min|least)\s+{_NUM}\b"
    rf"|\b{_NUM}\s+(?:lowest|smallest|least)\b", re.I)
_TOP_ONE_RE = re.compile(
    r"\b(?:highest|largest|biggest|maximum|max|most(?:\s+significant)?|greatest)\b", re.I)
_BOTTOM_ONE_RE = re.compile(r"\b(?:lowest|smallest|minimum|min|least)\b", re.I)
_RANK_RE = re.compile(r"\brank\b", re.I)

_INCREASE_RE = re.compile(
    r"\b(?:increas\w*|rise[ns]?|rose|risen|grew|grow\w*|gain\w*|higher|positive)\b", re.I)
_DECREASE_RE = re.compile(
    r"\b(?:decreas\w*|drop\w*|fell|fall\w*|declin\w*|lower|negative|reduc\w*)\b", re.I)

_UNCHANGED_RE = re.compile(
    r"\b(?:no\s+(?:change|variation|movement|variance)|zero\s+(?:variation|change|variance)"
    r"|unchanged|not\s+changed|remain(?:ed|s)?\s+(?:unchanged|same|constant)|identical"
    r"|same\s+values?|without\s+(?:any\s+)?change)\b", re.I)

_NEW_RE = re.compile(
    r"\b(?:newly\s+(?:populated|reported)|new(?:ly)?\s+(?:values?|elements?|reported|populated)"
    r"|(?:values?|data)\s+in\s+current\s+(?:period\s+)?but\s+not\s+in\s+previous"
    r"|previous\s+value\s+was\s+zero|was\s+zero\s+(?:and|but)\s+(?:now\s+)?non[- ]?zero"
    r"|not\s+in\s+previous)\b", re.I)
_BECAME_ZERO_RE = re.compile(
    r"\b(?:became\s+zero|become\s+zero|now\s+zero|are\s+zero\s+now|is\s+zero\s+now"
    r"|disappear\w*|dropped\s+to\s+zero|no\s+longer)\b", re.I)

_PCT_WORD_RE = re.compile(r"%|\bpercent(?:age)?\b|\bpct\b", re.I)
_ABS_WORD_RE = re.compile(r"\babsolute\b|\bdifference\b|\bamount\b|\bvalue\s+change\b", re.I)

_COLUMNWISE_RE = re.compile(
    r"\bcolumn[- ]?wise\b|\bwhich\s+columns?\b|\bcolumns?\s+(?:have|has|with)\b|\brank\s+columns?\b", re.I)
# "Which elements have changed?" — a question about what moved, so unchanged rows go.
_CHANGED_RE = re.compile(r"\b(?:which|what)\b[^.?]*?\bchanged\b", re.I)
_ELEMENTWISE_RE = re.compile(r"\belement[- ]?wise\b|\bwhich\s+elements?\b|\bevery\s+element\b|\ball\s+elements\b", re.I)

_TREND_RE = re.compile(
    r"\btrend\b|\bmonth[- ]on[- ]month\b|\bquarter[- ]on[- ]quarter\b|\byear[- ]on[- ]year\b"
    r"|\bcontinuously\b|\bconsecutive(?:ly)?\b|\bin\s+a\s+row\b|\bhistorical\b"
    r"|\blast\s+(?:\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(?:months?|quarters?|years?|periods?)\b", re.I)
_ANOMALY_RE = re.compile(
    r"\b(?:unusual|abnormal|anomal\w*|outliers?|exceptions?|deviations?|"
    r"need\s+(?:my\s+)?attention|significant\s+deviations?)\b", re.I)

# Words that mean "a number is coming that limits the result".
_GT = r"(?:more\s+than|greater\s+than|higher\s+than|bigger\s+than|above|exceed(?:s|ing)?|over|beyond|at\s+least|>=?)"
_LT = r"(?:less\s+than|lower\s+than|smaller\s+than|below|under|within|at\s+most|<=?)"

_BETWEEN_RE = re.compile(
    rf"\b(?:between|from)\s+{amount_pattern('1')}\s*(?:and|to|-)\s*{amount_pattern('2')}", re.I)
_GT_RE = re.compile(rf"(?:{_GT})\s*{amount_pattern('1')}", re.I)
_LT_RE = re.compile(rf"(?:{_LT})\s*{amount_pattern('1')}", re.I)
_EITHER_RE = re.compile(r"\beither\s+direction\b|\bboth\s+(?:directions|ways)\b|\bpositive\s+or\s+negative\b", re.I)


@dataclass
class Threshold:
    """A limit on the measure. `signed` limits the measure itself ("below -15%");
    otherwise it limits the MAGNITUDE ("more than 10%", "exceeds 20%")."""
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    signed: bool = False
    kind: str = "unknown"            # "pct" | "abs" | "unknown"

    def describe(self) -> str:
        unit = "%" if self.kind == "pct" else ""
        if self.min_value is not None and self.max_value is not None:
            return f"between {_fmt(self.min_value)}{unit} and {_fmt(self.max_value)}{unit}"
        if self.min_value is not None:
            return f"greater than {_fmt(self.min_value)}{unit}"
        if self.max_value is not None:
            return f"less than {_fmt(self.max_value)}{unit}"
        return ""


def _fmt(d: Decimal) -> str:
    return f"{d:,.2f}".rstrip("0").rstrip(".")


@dataclass
class AnalysisSpec:
    operation: str = "compare"       # compare | filter | rank | unchanged | new_removed | trend | anomaly
    measure: str = "unspecified"     # pct | abs | ambiguous | unspecified
    direction: str = "either"        # increase | decrease | either
    threshold: Optional[Threshold] = None
    top_n: Optional[int] = None
    bottom_n: Optional[int] = None
    group_by: str = "element"        # element | column
    new_removed: Optional[str] = None  # new | zero | both
    granularity: Optional[str] = None  # mom | qoq | yoy
    notes: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        """True when the query asks for nothing beyond the plain variance table."""
        return (self.operation == "compare" and self.threshold is None
                and self.direction == "either" and self.group_by == "element")

    def to_dict(self) -> dict:
        return {
            "operation": self.operation, "measure": self.measure, "direction": self.direction,
            "threshold": self.threshold.describe() if self.threshold else None,
            "top_n": self.top_n, "bottom_n": self.bottom_n, "group_by": self.group_by,
            "new_removed": self.new_removed, "granularity": self.granularity,
        }


def _to_n(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def _first_group(m: "re.Match[str]") -> str:
    return next(g for g in m.groups() if g)


def _parse_threshold(q: str, spec: AnalysisSpec) -> Optional[Threshold]:
    m = _BETWEEN_RE.search(q)
    if m:
        # "between 10 and 30%": the unit on either side applies to both.
        u = m.group("unit1") or m.group("unit2")
        a = build_amount(m.group("num1"), m.group("unit1"), m.group("neg1"), u)
        b = build_amount(m.group("num2"), m.group("unit2"), m.group("neg2"), u)
        if a and b:
            lo, hi = sorted((a[0], b[0]))
            kind = a[1] if a[1] != "unknown" else b[1]
            return Threshold(lo, hi, signed=(lo < 0), kind=kind)
    m = _GT_RE.search(q)
    if m:
        a = build_amount(m.group("num1"), m.group("unit1"), m.group("neg1"))
        if a:
            return Threshold(min_value=a[0], signed=(a[0] < 0), kind=a[1])
    m = _LT_RE.search(q)
    if m:
        a = build_amount(m.group("num1"), m.group("unit1"), m.group("neg1"))
        if a:
            return Threshold(max_value=a[0], signed=(a[0] < 0), kind=a[1])
    return None


def parse_operation(query: str) -> AnalysisSpec:
    """Read the operation, measure, direction and limits out of `query`."""
    q = (query or "").strip()
    spec = AnalysisSpec()
    if not q:
        return spec

    inc = bool(_INCREASE_RE.search(q))
    dec = bool(_DECREASE_RE.search(q))
    if inc and not dec:
        spec.direction = "increase"
    elif dec and not inc:
        spec.direction = "decrease"
    elif inc and dec:
        spec.direction = "either"

    spec.threshold = _parse_threshold(q, spec)
    if spec.threshold and _EITHER_RE.search(q):
        spec.direction = "either"

    # ── measure ──
    has_pct, has_abs = bool(_PCT_WORD_RE.search(q)), bool(_ABS_WORD_RE.search(q))
    if spec.threshold and spec.threshold.kind in ("pct", "abs"):
        spec.measure = spec.threshold.kind
    elif spec.threshold and not (has_pct or has_abs):
        spec.measure = "ambiguous"       # a bare number: percent or amount? (Phase 2 asks)
    elif has_pct and not has_abs:
        spec.measure = "pct"
    elif has_abs and not has_pct:
        spec.measure = "abs"
    elif has_pct and has_abs:
        spec.measure = "ambiguous"

    if _COLUMNWISE_RE.search(q) and not _ELEMENTWISE_RE.search(q):
        spec.group_by = "column"

    # ── operation (first match wins, most specific first) ──
    if _ANOMALY_RE.search(q):
        spec.operation = "anomaly"
    elif _TREND_RE.search(q):
        spec.operation = "trend"
        for key, pat in (("mom", r"month[- ]on[- ]month|\bmom\b"),
                         ("qoq", r"quarter[- ]on[- ]quarter|\bqoq\b"),
                         ("yoy", r"year[- ]on[- ]year|\byoy\b")):
            if re.search(pat, q, re.I):
                spec.granularity = key
    elif _UNCHANGED_RE.search(q):
        spec.operation = "unchanged"
    elif _NEW_RE.search(q) or _BECAME_ZERO_RE.search(q):
        spec.operation = "new_removed"
        new, gone = bool(_NEW_RE.search(q)), bool(_BECAME_ZERO_RE.search(q))
        spec.new_removed = "both" if new and gone else ("new" if new else "zero")
    else:
        m_top, m_bot = _TOP_N_RE.search(q), _BOTTOM_N_RE.search(q)
        if m_top:
            spec.top_n = _to_n(_first_group(m_top))
        elif m_bot:
            spec.bottom_n = _to_n(_first_group(m_bot))
        elif _BOTTOM_ONE_RE.search(q):
            spec.bottom_n = 1
        elif _TOP_ONE_RE.search(q) and not spec.threshold:
            spec.top_n = 1
        if spec.top_n or spec.bottom_n or _RANK_RE.search(q):
            spec.operation = "rank"
            if spec.measure == "unspecified" and spec.direction == "either":
                # "highest variation" — absolute or percentage? The caller asks.
                spec.measure = "ambiguous"
        elif spec.threshold is not None or spec.direction != "either" or _CHANGED_RE.search(q):
            spec.operation = "filter"

    return spec
