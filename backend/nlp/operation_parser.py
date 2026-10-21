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
    r"|disappear\w*|dropped\s+to\s+zero|no\s+longer"
    r"|current\s+value\s+is\s+zero|previous\s+value\s+was\s+non[- ]?zero|had\s+values?\s+previously)\b", re.I)

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
# "Which numbers require my attention?", "values requiring review" — same intent.
_ATTENTION_RE = re.compile(
    r"\b(?:requir\w*|deserv\w*|worth)\s+(?:my\s+|our\s+|a\s+)?(?:attention|review|a\s+look)\b"
    r"|\brequiring\s+review\b|\bunexpected(?:ly)?\b", re.I)

# "significant / major / big / important changes": a size condition with no number.
_SIGNIFICANT_RE = re.compile(
    r"\b(?:significant(?:ly)?|major|big|bigger|large|larger|important|material|notable)\b", re.I)
_SUMMARY_RE = re.compile(
    r"\b(?:summar\w+|executive|overview|management[- ]level|key\s+(?:changes|movements)"
    r"|should\s+know)\b", re.I)
# "Summarize increases and decreases separately"
_SPLIT_RE = re.compile(r"\bseparately\b", re.I)

# Sorting: "sort by percentage variation", "sort alphabetically", "highest increases first".
_SORT_RE = re.compile(r"\b(?:sort|sorted|order|ordered|arrange)\b", re.I)
_SORT_KEYS = [
    ("name", re.compile(r"\balphabetic\w*|\bby\s+(?:element\s+)?names?\b|\bby\s+element\b|\ba\s*(?:-|to)\s*z\b", re.I)),
    ("current", re.compile(r"\bby\s+current\b|\bcurrent\s+(?:value|amount)s?\b", re.I)),
    ("previous", re.compile(r"\bby\s+previous\b|\bprevious\s+(?:value|amount)s?\b", re.I)),
    ("pct", re.compile(r"%|\bpercent(?:age)?\b|\bpct\b", re.I)),
    ("abs", re.compile(r"\babsolute\b|\bdifference\b|\bamount\b|\bvariations?\b|\bchanges?\b", re.I)),
]
_ASC_RE = re.compile(
    r"\b(?:ascending|lowest\s+to\s+highest|smallest\s+to\s+largest|low\s+to\s+high|lowest\s+first|smallest\s+first)\b", re.I)
# "Show highest increases first" / "Show highest decreases first"
_FIRST_RE = re.compile(r"\b(?:highest|largest|biggest)\b[\w\s]*?\bfirst\b", re.I)

# Every numeric column: "all monetary columns", "all numeric columns".
_ALL_COLUMNS_RE = re.compile(r"\b(?:all|every)\s+(?:the\s+)?(?:monetary|numeric|amount|value)\s+columns?\b", re.I)

# A slice of the data by a descriptive column: "for Mumbai branch", "for product X",
# "only retail loans", "for corporate customers", "Asset-related elements".
_DIM_WORDS = (r"branch(?:es)?|products?|segments?|categor(?:y|ies)|departments?|regions?|zones?|"
              r"states?|cit(?:y|ies)|sectors?|portfolios?|business\s+lines?|lob")
_DIM_AFTER_RE = re.compile(
    # Value words never include a connector, so "in QCB_F013 for Mumbai branch" is "Mumbai".
    rf"\b(?:for|in|of|at|only|under)\s+(?:the\s+)?(?P<value>(?!(?:for|in|of|at|only|under|the)\b)[A-Za-z0-9][\w&\-.']*"
    rf"(?:\s+(?!(?:for|in|of|at|only|under|the)\b)[A-Za-z0-9][\w&\-.']*){{0,2}}?)"
    rf"\s+(?P<dim>{_DIM_WORDS})\b", re.I)
_DIM_BEFORE_RE = re.compile(rf"\b(?P<dim>{_DIM_WORDS})\s+(?P<value>[A-Z0-9][\w\-]*)\b")
_KIND_RE = re.compile(
    r"\b(?:only|for|just)\s+(?:the\s+)?(?P<value>[A-Za-z][\w\-]*)\s+"
    r"(?P<dim>loans?|customers?|accounts?|deposits?|advances?|borrowers?|exposures?)\b", re.I)
_RELATED_RE = re.compile(r"\b(?P<value>[A-Za-z][\w&]*)[- ]related\s+(?:elements?|items?|rows?)\b", re.I)
_NOT_A_VALUE = {"all", "each", "every", "this", "that", "these", "those", "the", "any", "a", "an",
                "my", "our", "particular", "which", "a particular", "same", "other"}

# A run in one direction across a window: "continuously increased", "5 periods in a row".
_STREAK_RE = re.compile(r"\bcontinuous(?:ly)?\b|\bconsecutive(?:ly)?\b|\bin\s+a\s+row\b|\bsteadily\b|\bevery\s+period\b", re.I)
# A window of time: the anomaly is then about movement over the series, not one comparison.
_WINDOW_RE = re.compile(
    r"\b(?:last|past|previous)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:months?|quarters?|years?|periods?)\b|\bover\s+time\b", re.I)

# "Explain how this is calculated", "show the formula"
_FORMULA_RE = re.compile(r"\bformula\b|\bhow\b[^?]*\bcalculat\w*|\bexplain\b[^?]*\bcalculat\w*", re.I)

# Words that mean "a number is coming that limits the result".
_GT = r"(?:more\s+than|greater\s+than|higher\s+than|bigger\s+than|above|exceed(?:s|ing)?|over|beyond|at\s+least|>=?)"
_LT = r"(?:less\s+than|lower\s+than|smaller\s+than|below|under|within|at\s+most|<=?)"

_BETWEEN_RE = re.compile(
    rf"\b(?:between|from)\s+{amount_pattern('1')}\s*(?:and|to|-)\s*{amount_pattern('2')}", re.I)
_GT_RE = re.compile(rf"(?:{_GT})\s*{amount_pattern('1')}", re.I)
_LT_RE = re.compile(rf"(?:{_LT})\s*{amount_pattern('1')}", re.I)
# A limit on the RECORD's own value, not its change: "records where amount is greater
# than ₹10 lakh", "where current value is above ₹1 crore".
_BASIS_RE = re.compile(
    r"\b(?:records?\s+where\s+(?:the\s+)?(?:amount|value)|(?P<which>current|previous|prior)\s+(?:value|amount|balance))"
    rf"\s+(?:is\s+)?(?:(?P<gt>{_GT})|(?P<lt>{_LT}))\s*{amount_pattern('1')}", re.I)
_EITHER_RE = re.compile(r"\beither\s+direction\b|\bboth\s+(?:directions|ways)\b|\bpositive\s+or\s+negative\b", re.I)


# ── Named targets ("element X", "the Asset column", "Assets, Liabilities and Capital columns")
# A name runs until punctuation or one of these words; it is never one of _NOT_A_NAME.
_NAME_STOP = (r"between|across|against|in|over|for|from|with|vs\.?|versus|compared|has|have|had"
              r"|is|was|were|are|increased|decreased|changed|changes|during|since|on|to|by|of|or")
_NAME = rf"(?:(?!(?:{_NAME_STOP})\b)[\w&/\-.']+)(?:\s+(?!(?:{_NAME_STOP})\b)[\w&/\-.']+)*"
_LIST = rf"{_NAME}(?:\s*(?:,|\band\b|&)\s*(?:the\s+)?{_NAME})*"
_ELEMENT_RE = re.compile(
    rf"(?<![\w-])(?<!which\s)(?<!each\s)(?<!every\s)(?<!any\s)(?<!an\s)element\s+(?![-\s]?wise\b)(?P<names>{_LIST})",
    re.I)
# "for the Asset element", "of X element"
_ELEMENT_SUFFIX_RE = re.compile(rf"\b(?:for|of|in)\s+(?:the\s+)?(?P<names>{_NAME})\s+element\b", re.I)
_COLUMN_RE = re.compile(
    rf"\b(?:compare|in|for|of|on)\s+(?:the\s+)?(?P<names>{_LIST})\s+columns?\b", re.I)
_VERBS = {"compare", "show", "give", "list", "find", "display", "get", "rank", "sort", "identify"}
# "the trend of Assets": a name that may be a column or an element — the caller decides.
_TREND_TARGET_RE = re.compile(rf"\b(?:trend|history|movement)\s+(?:of|for|in)\s+(?:the\s+)?(?P<names>{_NAME})", re.I)
_NOT_A_NAME = {
    "all", "these", "those", "this", "that", "each", "every", "any", "which", "the", "both",
    "two", "three", "four", "five", "monetary", "numeric", "amount", "other", "same",
}


def _split_names(raw: str) -> list[str]:
    parts = re.split(r"\s*(?:,|\band\b|&)\s*", raw.strip(" .?!'\""), flags=re.I)
    out = []
    for p in parts:
        p = re.sub(r"^(?:the\s+)?(?:elements?\s+)?", "", p.strip(), flags=re.I).strip(" .?!'\"")
        if p and p.split()[0].lower() not in _NOT_A_NAME and p.split()[0].lower() not in _VERBS:
            out.append(p)
    return out


# The whole name after "element" / "trend of", up to where the query moves on.
# Real row labels contain "and", "on", "of" ("Cash and Cash Equivalents",
# "Claims on Central Banks") which the word-level name pattern splits or cuts;
# analysis.py prefers this whole phrase when it matches a row exactly or is part of one.
_RAW_END = (r"(?=\s*(?:,|;|\bin\s+(?:the\s+)?(?:qcb|cims|raq|ale|f0\d)|\bin\s+(?:qcb|cims)\w*|\bvs\.?\b|\bversus\b"
            r"|\bcompared?\b|\bfor\s+the\s+(?:last|past)\b|\bover\s+the\b|\bbetween\b|\bfrom\b|\bwith\b"
            r"|\bincreased\b|\bdecreased\b|\bchanged\b|[.?!]|$))")
_ELEMENT_RAW_RE = re.compile(rf"(?<![\w-])element\s+(?![-\s]?wise\b)(?P<raw>.+?){_RAW_END}", re.I)
_TREND_RAW_RE = re.compile(rf"\b(?:trend|history|movement)\s+(?:of|for|in)\s+(?:the\s+)?(?P<raw>.+?){_RAW_END}", re.I)


def _raw(m: "Optional[re.Match[str]]") -> list[str]:
    if not m:
        return []
    raw = re.sub(r"^(?:has|is|the)\s+", "", m.group("raw").strip(" .?!'\""), flags=re.I)
    return [raw] if raw and len(raw) <= 120 else []


def _parse_targets(q: str, spec: "AnalysisSpec") -> None:
    spec.target_raw = _raw(_ELEMENT_RAW_RE.search(q))
    for m in _ELEMENT_RE.finditer(q):
        spec.target_elements += [n for n in _split_names(m.group("names")) if n not in spec.target_elements]
    if not spec.target_elements:
        m = _ELEMENT_SUFFIX_RE.search(q)
        if m:
            spec.target_elements = _split_names(m.group("names"))
    m = _COLUMN_RE.search(q)
    if m:
        # "for element X, compare all columns": X is the element, not a column.
        taken = {n.lower() for n in spec.target_elements}
        whole = " ".join(spec.target_raw).lower()
        spec.target_columns = [n for n in _split_names(m.group("names"))
                               if n.lower() not in taken and n.lower() not in whole]


@dataclass
class Threshold:
    """A limit on the measure. `signed` limits the measure itself ("below -15%");
    otherwise it limits the MAGNITUDE ("more than 10%", "exceeds 20%")."""
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    signed: bool = False
    kind: str = "unknown"            # "pct" | "abs" | "unknown"
    # What the limit applies to: the change (default), or the record's own
    # current / previous value ("where current value is above ₹1 crore").
    basis: str = "change"            # change | current | previous

    def describe(self) -> str:
        unit = "%" if self.kind == "pct" else ""
        if self.min_value is not None and self.max_value is not None:
            text = f"between {_fmt(self.min_value)}{unit} and {_fmt(self.max_value)}{unit}"
        elif self.min_value is not None:
            text = f"greater than {_fmt(self.min_value)}{unit}"
        elif self.max_value is not None:
            text = f"less than {_fmt(self.max_value)}{unit}"
        else:
            return ""
        return text if self.basis == "change" else f"{self.basis} value {text}"


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
    # Names the user typed, verbatim; analysis.py matches them against the real
    # row labels / column descriptions (and says so when nothing matches).
    target_elements: list[str] = field(default_factory=list)
    target_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # ── additive (scenario groups 1, 3, 13–18, 22) — all default to "not asked" ──
    significant: bool = False          # "significant / major changes", no number given
    summary: bool = False              # "summarize", "executive summary"
    split_directions: bool = False     # "increases and decreases separately"
    sort_by: Optional[str] = None      # abs | pct | current | previous | name
    sort_desc: bool = True
    all_columns: bool = False          # "all (monetary) columns": every numeric column
    record_filter: Optional[Threshold] = None  # a limit on the record's value, besides `threshold`
    explain_formula: bool = False
    streak: bool = False               # "continuously increased over the last 5 periods"
    unusual: bool = False              # trend + "unusual movement": z-score over the series
    target_any: list[str] = field(default_factory=list)  # "trend of Assets": column or element
    # (dimension word or "", value): "for Mumbai branch" -> ("branch", "Mumbai").
    # analysis.py keeps the rows whose descriptive columns hold that value.
    dimension_filters: list[tuple[str, str]] = field(default_factory=list)
    # The whole element phrase as typed ("Cash and Cash Equivalents"); see _RAW_END.
    target_raw: list[str] = field(default_factory=list)

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
            "target_elements": list(self.target_elements), "target_columns": list(self.target_columns),
            "significant": self.significant, "summary": self.summary,
            "split_directions": self.split_directions, "sort_by": self.sort_by,
            "sort_desc": self.sort_desc, "all_columns": self.all_columns,
            "record_filter": self.record_filter.describe() if self.record_filter else None,
            "streak": self.streak, "unusual": self.unusual,
            "dimension_filters": [f"{d or 'value'}: {v}" for d, v in self.dimension_filters],
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


_PLURAL_RE = re.compile(r"\b(?:elements|columns|items|values|rows|records|lines)\b", re.I)


def _default_rank_n() -> int:
    try:
        from .nlp_config import DEFAULT_RANK_N
        return max(int(DEFAULT_RANK_N), 1)
    except Exception:
        return 10


def _parse_record_filter(q: str) -> tuple[Optional[Threshold], str]:
    """A limit on the record's own value ("where current value is above ₹1 crore"),
    and `q` with that phrase blanked so it is not read again as a limit on the change."""
    m = _BASIS_RE.search(q)
    if not m:
        return None, q
    a = build_amount(m.group("num1"), m.group("unit1"), m.group("neg1"))
    if not a or a[1] == "pct":
        return None, q
    which = (m.group("which") or "current").lower()
    basis = "previous" if which in ("previous", "prior") else "current"
    t = (Threshold(min_value=a[0], signed=True, kind="abs", basis=basis) if m.group("gt")
         else Threshold(max_value=a[0], signed=True, kind="abs", basis=basis))
    return t, q[:m.start()] + " " * (m.end() - m.start()) + q[m.end():]


def _parse_sort(q: str, spec: "AnalysisSpec") -> bool:
    if not (_SORT_RE.search(q) or _FIRST_RE.search(q)):
        return False
    spec.sort_by = next((key for key, pat in _SORT_KEYS if pat.search(q)), "measure")
    if spec.sort_by in ("abs", "pct") and not re.search(r"\babsolute\b|\bdifference\b|%|percent|\bpct\b", q, re.I):
        spec.sort_by = "measure"        # "sort variations": by the effective measure
    spec.sort_desc = not (_ASC_RE.search(q) or (spec.sort_by == "name" and not re.search(r"\bdesc\w*|z\s*(?:-|to)\s*a\b", q, re.I)))
    return True


def _parse_dimensions(q: str, spec: "AnalysisSpec") -> None:
    seen: set = set()

    def add(dim: str, value: str) -> None:
        value = value.strip(" .,'\"")
        words = value.lower().split()
        if not value or value.lower() in _NOT_A_VALUE or (words and words[0] in _NOT_A_VALUE):
            return
        key = (dim.lower(), value.lower())
        if key not in seen:
            seen.add(key)
            spec.dimension_filters.append((dim.lower(), value))

    for m in _DIM_AFTER_RE.finditer(q):
        add(re.sub(r"(?:es|s|ies)$", "", m.group("dim").lower()) or m.group("dim"), m.group("value"))
    for m in _DIM_BEFORE_RE.finditer(q):
        add(re.sub(r"(?:es|s)$", "", m.group("dim").lower()), m.group("value"))
    for m in _KIND_RE.finditer(q):
        add("", m.group("value"))
    for m in _RELATED_RE.finditer(q):
        add("", m.group("value"))


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

    spec.record_filter, q_change = _parse_record_filter(q)
    spec.threshold = _parse_threshold(q_change, spec)
    if spec.threshold and _EITHER_RE.search(q):
        spec.direction = "either"

    # ── measure ──
    has_pct, has_abs = bool(_PCT_WORD_RE.search(q_change)), bool(_ABS_WORD_RE.search(q_change))
    if spec.threshold and spec.threshold.kind in ("pct", "abs"):
        spec.measure = spec.threshold.kind
    elif spec.threshold and not (has_pct or has_abs):
        spec.measure = "ambiguous"       # a bare number: percent or amount? (the caller asks)
    elif has_pct and not has_abs:
        spec.measure = "pct"
    elif has_abs and not has_pct:
        spec.measure = "abs"
    elif has_pct and has_abs:
        spec.measure = "ambiguous"

    if _COLUMNWISE_RE.search(q) and not _ELEMENTWISE_RE.search(q):
        spec.group_by = "column"

    _parse_targets(q, spec)
    spec.all_columns = bool(_ALL_COLUMNS_RE.search(q))
    spec.summary = bool(_SUMMARY_RE.search(q))
    spec.split_directions = bool(_SPLIT_RE.search(q))
    spec.explain_formula = bool(_FORMULA_RE.search(q))
    sorting = _parse_sort(q, spec)

    spec.streak = bool(_STREAK_RE.search(q))
    _parse_dimensions(q, spec)

    # ── operation (first match wins, most specific first) ──
    anomaly = bool(_ANOMALY_RE.search(q) or _ATTENTION_RE.search(q))
    if anomaly and _WINDOW_RE.search(q):
        # "unusual movement over the last six months": judged across the series.
        spec.operation, spec.unusual = "trend", True
    elif anomaly:
        spec.operation = "anomaly"
    if spec.operation == "trend" or (not anomaly and _TREND_RE.search(q)):
        spec.operation = "trend"
        m = _TREND_TARGET_RE.search(q)
        if m and not spec.target_elements and not spec.target_columns:
            spec.target_any = _split_names(m.group("names"))
            spec.target_raw = spec.target_raw or _raw(_TREND_RAW_RE.search(q))
        for key, pat in (("mom", r"month[- ]on[- ]month|\bmom\b"),
                         ("qoq", r"quarter[- ]on[- ]quarter|\bqoq\b"),
                         ("yoy", r"year[- ]on[- ]year|\byoy\b")):
            if re.search(pat, q, re.I):
                spec.granularity = key
    elif spec.operation == "anomaly":
        pass
    elif _UNCHANGED_RE.search(q):
        spec.operation = "unchanged"
    elif _NEW_RE.search(q) or _BECAME_ZERO_RE.search(q):
        spec.operation = "new_removed"
        new, gone = bool(_NEW_RE.search(q)), bool(_BECAME_ZERO_RE.search(q))
        spec.new_removed = "both" if new and gone else ("new" if new else "zero")
    else:
        m_top, m_bot = _TOP_N_RE.search(q), _BOTTOM_N_RE.search(q)
        plural_n = _default_rank_n() if _PLURAL_RE.search(q) else 1
        if m_top:
            spec.top_n = _to_n(_first_group(m_top))
        elif m_bot:
            spec.bottom_n = _to_n(_first_group(m_bot))
        elif sorting:
            pass                         # "sort … highest to lowest" orders, it does not cut
        elif _BOTTOM_ONE_RE.search(q):
            spec.bottom_n = plural_n
        elif _TOP_ONE_RE.search(q) and not spec.threshold:
            spec.top_n = plural_n
        if spec.top_n or spec.bottom_n or _RANK_RE.search(q):
            spec.operation = "rank"
            if spec.measure == "unspecified" and spec.direction == "either":
                # "highest variation" — absolute or percentage? The caller asks.
                spec.measure = "ambiguous"
        elif (spec.threshold is not None or spec.record_filter is not None
              or spec.direction != "either" or _CHANGED_RE.search(q)):
            spec.operation = "filter"

    # "significant changes" with no number: a size condition the caller fills in
    # (asks, or applies the configured default). With a number or a ranking, the
    # word adds nothing.
    if (_SIGNIFICANT_RE.search(q) and spec.threshold is None
            and spec.operation in ("compare", "filter")):
        spec.significant = True
    return spec
