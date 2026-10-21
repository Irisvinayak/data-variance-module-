# conversation.py — follow-up questions and query refinements for /variance/nlresolve.
#
# Two things happen between turns, both without a server-side session:
#
#   * Refinement. A question the module asks back ("absolute or percentage?",
#     "what counts as significant?", "March against which period?") is answered
#     by a short phrase appended to the query ("by percentage change"). The
#     phrases travel in resolved_context["refine"], so every later round of the
#     same question re-reads them.
#
#   * Follow-up. After an answer the server hands back conversation_state (the
#     turns so far, the return/table/dates it used, the identifiers it showed).
#     The browser sends it with the next question; a follow-up ("only
#     decreases", "more than 20%", "show details of the first one", "now for
#     the last 6 months") is merged into the previous request instead of
#     starting over.
#
# Pure functions: what the turn means is decided here, the numbers are still
# computed by service.compute_variance and analysis.py.

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from typing import Any, Optional

from .operation_parser import AnalysisSpec, parse_operation

MAX_TURNS = 8
MAX_IDENTIFIERS = 50

# ── refinement questions ──────────────────────────────────────────────────────
MEASURE_OPTIONS = [
    {"id": "pct", "label": "Percentage change"},
    {"id": "abs", "label": "Absolute change (amount)"},
]
PERIOD_OPTIONS = [
    {"id": "previous", "label": "The previous reporting period"},
    {"id": "last_year", "label": "The same period last year"},
]
THRESHOLD_OPTIONS = [
    {"id": "10", "label": "More than 10%"},
    {"id": "20", "label": "More than 20%"},
    {"id": "50", "label": "More than 50%"},
]

_REFINE_TEXT = {
    "measure": {"pct": "by percentage change", "abs": "by absolute change"},
    "period": {"previous": "compared with the previous period",
               "last_year": "compared with the same period last year"},
}
# A threshold the user typed: "15", "15%", "5 lakh", "2.5 crore", "1 million".
_THRESHOLD_ANSWER_RE = re.compile(
    r"^\s*[₹$]?\s*\d+(?:\.\d+)?\s*(?:%|percent|lakhs?|lacs?|crores?|cr|millions?|mn|billions?|bn|thousand|k)?\s*$", re.I)


class InvalidAnswer(ValueError):
    pass


def refinement_text(dimension: str, answer: Optional[str], skip: str, default_pct: float) -> str:
    """The phrase that answers a refinement question. A skip answers with the default."""
    if dimension == "threshold":
        if not answer or answer == skip:
            return f"with change more than {default_pct:g}%"
        a = answer.strip()
        if not _THRESHOLD_ANSWER_RE.match(a):
            raise InvalidAnswer("Please give the threshold as a number, e.g. 15%, or an amount such as 5 lakh.")
        if re.fullmatch(r"\d+(?:\.\d+)?", a):
            a += "%"
        return f"with change more than {a}"
    table = _REFINE_TEXT[dimension]
    if not answer or answer == skip:
        return next(iter(table.values()))
    if answer not in table:
        raise InvalidAnswer("Unknown answer.")
    return table[answer]


def effective_query(typed: str, refine: list[str]) -> str:
    return typed if not refine else typed.rstrip(" .?!") + ". " + ". ".join(refine)


def clean_refine(raw: Any) -> list[str]:
    """resolved_context['refine'] comes back from the browser: keep only phrases
    this module could have produced."""
    if not isinstance(raw, list):
        return []
    allowed = {t for d in _REFINE_TEXT.values() for t in d.values()}
    out = []
    for r in raw[:5]:
        if isinstance(r, str) and (r in allowed or re.fullmatch(r"with change more than [₹$]?\s*[\d.]+\s*[%\w]*", r)):
            out.append(r)
    return out


_BARE_MONTH_ONLY_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)


def needs_period_question(query: str, fiscal_start: int, today: date) -> Optional[str]:
    """'Compare March' — one month, no year, nothing to compare it with. Returns the month."""
    from .date_intent import parse_date_intent

    intent = parse_date_intent(query, fiscal_start, today=today)
    a = intent.anchor
    if (a and a.granularity == "month" and not re.search(r"\d", a.text) and not intent.targets
            and not intent.relative and not intent.xox and not intent.range and not intent.since):
        return a.text.strip()
    return None


# ── follow-ups ────────────────────────────────────────────────────────────────
_FOLLOWUP_RE = re.compile(
    r"^\s*(?:ok(?:ay)?[, ]+)?(?:now\b|then\b|and\b|also\b|but\b|only\b|just\b|instead\b|same\b|"
    r"compare\s+(?:it\s+|this\s+|that\s+)?(?:with|to|against)\b|"
    r"top\b|bottom\b|more\s+than\b|less\s+than\b|above\b|below\b|over\b|under\b|greater\b|sort\b|"
    r"remove\b|exclude\b|hide\b|drop\b|drill\b|what\s+about\b|why\s+did\b|which\s+records\b|"
    r"filter\b|keep\s+only\b|show\s+(?:me\s+)?(?:only|just|the\s+top|top|the\s+bottom|bottom|details?|"
    r"the\s+details?|more|both|absolute|percentage|the\s+underlying|underlying|the\s+source|source|"
    r"the\s+difference|current\s+and\s+previous|increases?\s+only|decreases?\s+only))", re.I)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
             "fifth": 5, "5th": 5, "top": 1, "last": -1}
_ORDINAL_RE = re.compile(
    r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|top|last)\s+(?:one|element|item|row|result|"
    r"variation|change|entry)\b", re.I)
_SAME_ELEMENT_RE = re.compile(r"\b(?:this|that|the\s+same|same)\s+(?:element|one|item|variation|row)\b", re.I)
_DRILL_RE = re.compile(
    r"\b(?:details?|drill\s*(?:down)?|underlying|source\s+data|records?|why\s+did|contributed|"
    r"current\s+and\s+previous\s+values?|the\s+difference)\b", re.I)
# "Now do the same analysis for liabilities", "same for capital": a different metric, same operation.
_SWITCH_RE = re.compile(
    r"\bsame\s+(?:analysis|thing|check)\b|\bsame\s+(?:for|on)\b|\bnow\s+(?:do\s+)?(?:it|this|the\s+same)\s+(?:for|on)\b", re.I)
_INSTEAD_RE = re.compile(r"\binstead\b|\brather\s+than\b|\bin\s+place\s+of\b", re.I)


def clean_state(raw: Any) -> Optional[dict]:
    """conversation_state comes back from the browser; keep only well-formed fields.
    Identifiers are only ever used to MATCH rows of a fresh result, never in SQL."""
    if not isinstance(raw, dict):
        return None
    turns = [t for t in (raw.get("turns") or []) if isinstance(t, str) and t.strip()][-MAX_TURNS:]
    if not turns:
        return None
    ids = [str(i) for i in (raw.get("identifiers") or []) if isinstance(i, (str, int))][:MAX_IDENTIFIERS]
    dates = [d for d in (raw.get("dates") or []) if isinstance(d, str) and re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{4}", d)][:13]
    return {
        "turns": turns,
        "return_id": raw.get("return_id") if isinstance(raw.get("return_id"), str) else None,
        "table": raw.get("table") if isinstance(raw.get("table"), str) else None,
        "dates": dates,
        "identifiers": ids,
        "focus": str(raw["focus"]) if isinstance(raw.get("focus"), (str, int)) else None,
    }


def is_followup(text: str) -> bool:
    return bool(_FOLLOWUP_RE.search(text) or _ORDINAL_RE.search(text) or _SAME_ELEMENT_RE.search(text)
                or _SWITCH_RE.search(text))


def _merge(prev: AnalysisSpec, fu: AnalysisSpec, text: str) -> AnalysisSpec:
    """`prev` with every field the follow-up states explicitly replaced."""
    new = replace(prev, notes=[], target_elements=list(prev.target_elements),
                  target_columns=list(prev.target_columns), target_any=list(prev.target_any))
    if fu.operation in ("unchanged", "new_removed", "anomaly", "trend"):
        new.operation = fu.operation
        new.new_removed, new.granularity = fu.new_removed, fu.granularity
        new.streak, new.unusual = fu.streak, fu.unusual
        if fu.operation != "trend":
            new.top_n = new.bottom_n = None
    if fu.direction != "either":
        new.direction = fu.direction
    if fu.threshold is not None:
        new.threshold = fu.threshold
        new.significant = False
    if fu.record_filter is not None:
        new.record_filter = fu.record_filter
    if fu.measure in ("pct", "abs"):
        new.measure = fu.measure
    elif _INSTEAD_RE.search(text) and fu.measure == "ambiguous":
        # "absolute instead of percentage": the first-named measure is the new one.
        a, p = re.search(r"\babsolute|\bamount|\bdifference", text, re.I), re.search(r"%|\bpercent", text, re.I)
        if a and p:
            new.measure = "abs" if a.start() < p.start() else "pct"
    if fu.top_n or fu.bottom_n:
        new.top_n, new.bottom_n = fu.top_n, fu.bottom_n
        if new.operation in ("compare", "filter"):
            new.operation = "rank"
    if fu.sort_by:
        new.sort_by, new.sort_desc = fu.sort_by, fu.sort_desc
    if fu.group_by == "column":
        new.group_by = "column"
    if fu.target_elements:
        new.target_elements = list(fu.target_elements)
    if fu.target_columns:
        new.target_columns = list(fu.target_columns)
    if fu.significant and new.threshold is None:
        new.significant = True
    new.summary = new.summary or fu.summary
    new.split_directions = new.split_directions or fu.split_directions
    new.explain_formula = fu.explain_formula
    if new.operation == "compare" and (new.threshold or new.direction != "either" or new.record_filter):
        new.operation = "filter"
    if new.measure == "unspecified" and new.operation == "rank" and new.direction == "either":
        new.measure = "ambiguous"
    return new


def merged_spec(turns: list[str]) -> AnalysisSpec:
    """The spec of a whole conversation: the first turn, refined by each follow-up."""
    spec = parse_operation(turns[0])
    for t in turns[1:]:
        spec = _merge(spec, parse_operation(t), t)
    return spec


def focus_of(text: str, state: dict) -> Optional[str]:
    """The identifier a follow-up points at: "the first one", "this element"."""
    ids = state.get("identifiers") or []
    m = _ORDINAL_RE.search(text)
    if m and ids:
        k = _ORDINALS[m.group(1).lower()]
        idx = len(ids) - 1 if k == -1 else k - 1
        return ids[idx] if 0 <= idx < len(ids) else None
    if _SAME_ELEMENT_RE.search(text):
        return state.get("focus") or (ids[0] if len(ids) == 1 else None)
    return None


def plan_followup(text: str, state: dict) -> dict:
    """How to run a follow-up turn:
      spec       — the merged AnalysisSpec
      query      — the text retrieval and intent read (the conversation's first turn,
                   or the new turn when it switches metric)
      pin_table  — keep the previous return/table (False when switching metric)
      date_query — the new turn's own text (its dates, if any, win)
      focus      — an identifier being drilled into, or None
      drill      — True when the turn asks for one item's detail
      error      — a message when the turn points at something that does not exist"""
    turns = (state["turns"] + [text])[-MAX_TURNS:]
    switching = bool(_SWITCH_RE.search(text))
    focus = focus_of(text, state)
    plan: dict[str, Any] = {"turns": turns, "focus": focus, "drill": False, "error": None,
                            "pin_table": not switching, "date_query": text,
                            "query": text if switching else state["turns"][0]}
    pointed = bool(_ORDINAL_RE.search(text) or _SAME_ELEMENT_RE.search(text))
    if pointed and focus is None:
        plan["error"] = ("I can't tell which item you mean — the previous answer listed "
                         f"{len(state.get('identifiers') or [])} item(s). Please name the element.")
        return plan

    spec = merged_spec(turns)
    if switching:
        spec = replace(spec, target_elements=[], target_columns=[], target_any=[])
    if focus is not None:
        fu = parse_operation(text)
        if fu.operation == "trend":
            # "Compare the same element for the last 6 months": a series for that element only.
            spec = replace(fu, target_elements=[focus], target_columns=list(spec.target_columns), notes=[])
        else:
            # Drill-down: that one element in full, without the filters that found it.
            spec = AnalysisSpec(target_elements=[focus], measure=spec.measure if spec.measure in ("pct", "abs") else "unspecified",
                                target_columns=list(spec.target_columns), explain_formula=fu.explain_formula)
            plan["drill"] = True
    elif _DRILL_RE.search(text) and len(state.get("identifiers") or []) == 1:
        spec = AnalysisSpec(target_elements=[state["identifiers"][0]])
        plan["focus"], plan["drill"] = state["identifiers"][0], True
    plan["spec"] = spec
    return plan


def new_state(turns: list[str], result: dict, return_id: Optional[str], table: Optional[str],
              focus: Optional[str]) -> dict:
    interp = result.get("interpretation") or {}
    return {
        "turns": turns[-MAX_TURNS:],
        "return_id": return_id,
        "table": table,
        "dates": list(interp.get("comparison_dates") or [])[:13],
        "identifiers": [str(r.get("identifier")) for r in (result.get("rows") or [])
                        if r.get("identifier") is not None][:MAX_IDENTIFIERS],
        "focus": focus,
    }


def drill_down(result: dict, focus: str) -> Optional[dict]:
    """Current, previous, absolute and percentage change of one element, per column,
    plus its source record as read from the table."""
    row = next((r for r in result.get("rows") or [] if str(r.get("identifier")) == str(focus)), None)
    if row is None and len(result.get("rows") or []) == 1:
        row = result["rows"][0]
    if row is None:
        return None
    cells = (row.get("analysis") or {}).get("cells") or {}
    prev = (row.get("previous") or {}).get("previous_1") or {}
    current = row.get("current") or {}
    cols = []
    for col in result.get("columns") or []:
        metric = prev.get(col) or {}
        cell = cells.get(col) or {}
        cols.append({
            "column": col,
            "current": current.get(col.upper()),
            "previous": metric.get("value"),
            "change": cell.get("change"),
            "pct_change": cell.get("pct_change"),
            "status": cell.get("status"),
        })
    return {
        "identifier": row.get("identifier"),
        "label": row.get("display_label") or row.get("identifier"),
        "columns": cols,
        "record": current,
        "note": ("'record' is the element's row as stored in the table for the current period. "
                 "Contributing transactions below this level are not part of the return's data."),
    }
