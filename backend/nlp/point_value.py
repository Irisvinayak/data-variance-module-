"""Answer a question about ONE figure with that figure, current vs previous.

"What is total loan assets for Term Loans as of 31-Mar-2025" asks for one number.
compute_variance() returns every row and every numeric column of the table, and the
general narrowing (_restrict_result_columns, row_focus) keeps more than that whenever
it is unsure. This module narrows further, but only when it can prove the answer:

  * one measure  — the column the user named, or the only one whose description
                   shares the most words with the question (a tie -> no card);
  * one line item — a row-label the retriever matched AND whose words the user typed,
                   or the only row whose label holds every remaining content word
                   (the measure's own words are taken out first: "total loan assets
                   for Term Loans" looks for "term loan" in the labels, not "asset");
  * one previous period — the card compares with previous_1 only, and is not built
                   when the user asked for several periods.

When any of that fails, the result is returned UNCHANGED (plus a note when the user
clearly asked for one value), so this can only ever remove rows the user did not ask
for, never pick the wrong one. Every number on the card is read from compute_variance's
own output (value / change / pct_change), never recomputed here.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from .analysis import _norm
from .row_focus import _GENERIC, _canon

__all__ = [
    "is_value_question", "wants_full_table", "lookup_applies",
    "pick_column", "apply_value_lookup",
]

# "what is / what was / what's", "value of", "how much", "amount of", "figure for" …
_VALUE_PHRASE = re.compile(
    r"\b(what\s*(?:is|was|are|were)|what'?s|value\s+of|how\s+much|amount\s+of|"
    r"figure\s+(?:of|for)|balance\s+of|tell\s+me\s+the|give\s+me\s+the|"
    r"current\s+value|latest\s+value|show\s+(?:me\s+)?the\s+value)\b",
    re.IGNORECASE,
)
# Ways of saying "not one value, the whole thing" — these turn lookup off.
_FULL_TABLE = re.compile(
    r"\b(all\s+(?:the\s+)?(?:rows|line\s*items|items|elements|records|entries)|"
    r"full\s+table|whole\s+table|entire\s+table|every\s+(?:row|line\s*item|element)|"
    r"each\s+(?:row|line\s*item|element)|list\s+(?:all|every))\b",
    re.IGNORECASE,
)
# "(iii) Term Loans", "1. Cash", "a) Deposits": the numbering is not part of the name.
_LEADING_NUMBER = re.compile(
    r"^\s*(\(\s*[ivxlc]+\s*\)|\(\s*[a-z0-9]{1,3}\s*\)|[ivxlc]+\.|[a-z0-9]{1,3}[.)])\s*",
    re.IGNORECASE,
)
_MAX_NAMED_OPTIONS = 5


def is_value_question(query: str) -> bool:
    return bool(_VALUE_PHRASE.search(query or ""))


def wants_full_table(query: str) -> bool:
    return bool(_FULL_TABLE.search(query or ""))


def lookup_applies(spec: Any, query: str) -> bool:
    """True only for a plain "this figure, now vs before" question: no ranking,
    filtering, threshold, trend, summary, sorting, dimension split or "all rows"."""
    if spec is None or wants_full_table(query):
        return False
    if getattr(spec, "operation", "compare") != "compare":
        return False
    if (spec.threshold is not None or spec.direction != "either" or spec.group_by != "element"
            or spec.top_n is not None or spec.bottom_n is not None or spec.new_removed):
        return False
    if (spec.all_columns or spec.summary or spec.significant or spec.split_directions
            or spec.sort_by or spec.record_filter is not None or spec.streak or spec.unusual):
        return False
    if len(spec.target_elements or []) > 1 or len(spec.target_columns or []) > 1:
        return False
    return True


# ── tokens ────────────────────────────────────────────────────────────────────
def _label_norm(text: Any) -> str:
    return _norm(_LEADING_NUMBER.sub("", str(text or "")))


def _tokens(text: Any) -> set[str]:
    return {_canon(t) for t in _label_norm(text).split() if t}


def _content(text: Any) -> set[str]:
    return {t for t in _tokens(text) if t not in _GENERIC and not t.isdigit() and len(t) >= 2}


# ── measure ───────────────────────────────────────────────────────────────────
def pick_column(
    columns: list[str], metric_text: str, describe: Callable[[str], Optional[str]],
) -> Optional[str]:
    """The single column the question is about, or None when it cannot be told apart.
    Scores = words shared between the question and the column's description/name."""
    if len(columns) == 1:
        return columns[0]
    if not columns:
        return None
    wanted = _content(metric_text)
    if not wanted:
        return None
    scored = []
    for col in columns:
        words = _content(describe(col) or "") | _content(col.replace("_", " "))
        scored.append((len(wanted & words), col))
    scored.sort(key=lambda s: s[0], reverse=True)
    if scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


# ── line item ─────────────────────────────────────────────────────────────────
def _row_label_values(row: dict[str, Any], label_key: str) -> list[str]:
    """Texts that name a row: its display label, identifier, and its text cells."""
    texts = [str(row.get("display_label") or ""), str(row.get("identifier") or "")]
    cells = row.get(label_key) or {}
    if isinstance(cells, dict):
        texts += [str(v) for v in cells.values() if isinstance(v, str)]
    return [t for t in texts if t.strip()]


def _match_rows(
    rows: list[dict[str, Any]], label_key: str, query: str, metric_text: str,
    column_words: set[str], matched_labels: list[dict[str, Any]], table_name: str,
) -> list[int]:
    """Indexes of the rows the question names; [] when none can be singled out."""
    if not rows:
        return []
    query_words = _content(query)

    # 1. A row label the retriever matched for THIS table, whose words the user typed.
    typed_labels = set()
    for lbl in matched_labels or []:
        if str(lbl.get("table") or "").upper() != (table_name or "").upper():
            continue
        words = _content(lbl.get("value"))
        if words and words <= query_words:
            typed_labels.add(_label_norm(lbl.get("value")))
    if typed_labels:
        hits = [i for i, r in enumerate(rows)
                if any(_label_norm(t) in typed_labels for t in _row_label_values(r, label_key))]
        if len(hits) == 1:
            return hits

    # 2. Rows whose label holds every remaining content word (measure words removed).
    words = _content(metric_text) - column_words
    if words:
        hits = [i for i, r in enumerate(rows)
                if any(words <= _tokens(t) for t in _row_label_values(r, label_key))]
        if len(hits) > 1:
            # Prefer the label whose every word was typed ("Term Loans" over
            # "Term Loans - Overdue" when the user never said "overdue").
            typed = _content(metric_text) | query_words
            exact = [i for i in hits
                     if any(_content(t) and _content(t) <= typed
                            for t in _row_label_values(rows[i], label_key)[:2])]
            if len(exact) == 1:
                return exact
        return hits
    return []


def _dec(v: Any) -> Optional[Decimal]:
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        return None
    try:
        return Decimal(str(v).replace(",", "").rstrip("%").strip())
    except (InvalidOperation, ValueError):
        return None


def _metric(m: Any) -> Any:
    """compute_variance metric cells are {"value": ..., "color": ...}; the card wants the value."""
    if isinstance(m, dict):
        v = m.get("value")
        return None if v in (None, "N/A") else v
    return m


def _card(
    *, item: str, measure: str, column: str, current_date: Any, previous_date: Any,
    current: Any, previous: Any, change: Any, pct_change: Any, note: Optional[str],
) -> dict[str, Any]:
    delta = _dec(change)
    direction = None if delta is None else ("up" if delta > 0 else "down" if delta < 0 else "unchanged")
    return {
        "item": item, "measure": measure, "column": column,
        "current": {"date": current_date, "value": current},
        "previous": {"date": previous_date, "value": previous},
        "change": change, "pct_change": pct_change, "direction": direction,
        "note": note,
    }


def apply_value_lookup(
    computed: dict[str, Any], *, query: str, metric_text: str, table_name: str,
    describe: Callable[[str], Optional[str]], matched_labels: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], list[str]]:
    """(result narrowed to one line item with a `value_card`, notes) — or the result
    unchanged when one figure cannot be proven."""
    notes: list[str] = []
    explicit = is_value_question(query)
    columns = computed.get("columns") or []
    if len(columns) != 1:
        if explicit and len(columns) > 1:
            names = ", ".join((describe(c) or c) for c in columns[:_MAX_NAMED_OPTIONS])
            notes.append(f"More than one measure matches this question ({names}); "
                         "name one to see a single value.")
        return computed, notes
    col = columns[0]

    periods = computed.get("comparison_periods") or []
    if len(periods) > 1:
        return computed, notes          # several periods were asked for: keep the table

    rows = computed.get("rows") or []
    removed = [r for r in (computed.get("removed_rows") or []) if r.get("period") in (None, "previous_1")]
    column_words = _content(describe(col) or "") | _content(col.replace("_", " "))

    if len(rows) == 1:
        hits, source = [0], "rows"
    else:
        hits = _match_rows(rows, "current", query, metric_text, column_words,
                           matched_labels or [], table_name)
        source = "rows"
        if not hits:
            r_hits = _match_rows(removed, "previous", query, metric_text, column_words,
                                 matched_labels or [], table_name)
            if len(r_hits) == 1:
                hits, source = r_hits, "removed"

    if len(hits) != 1:
        if explicit and len(hits) > 1:
            pool = rows if source == "rows" else removed
            sample = ", ".join(f"'{pool[i].get('display_label') or pool[i].get('identifier')}'"
                               for i in hits[:_MAX_NAMED_OPTIONS])
            notes.append(f"{len(hits)} line items match this question ({sample}"
                         f"{', …' if len(hits) > _MAX_NAMED_OPTIONS else ''}); "
                         "name one to see a single value.")
        return computed, notes

    current_date = computed.get("reporting_date")
    previous_date = periods[0] if periods else None
    measure = describe(col) or col

    if source == "rows":
        row = rows[hits[0]]
        item = str(row.get("display_label") or row.get("identifier") or "")
        if item and item in set(computed.get("duplicate_identifiers") or []) or (
                row.get("identifier") in set(computed.get("duplicate_identifiers") or [])):
            notes.append(f"'{item}' appears more than once in the same period, so a single "
                         "value cannot be given reliably; showing the matching rows.")
            return computed, notes
        current = (row.get("current") or {}).get(col.upper())
        m = ((row.get("previous") or {}).get("previous_1") or {}).get(col)
        if not periods:
            note = "There is no earlier period in this table to compare with."
            previous = change = pct = None
        elif not m:
            note = f"'{item}' was not reported on {previous_date}, so there is no previous value."
            previous = change = pct = None
        else:
            note = None
            previous, change, pct = m.get("value"), _metric(m.get("change")), _metric(m.get("pct_change"))
        card = _card(item=item, measure=measure, column=col, current_date=current_date,
                     previous_date=previous_date, current=current, previous=previous,
                     change=change, pct_change=pct, note=note)
        out = {**computed, "rows": [row], "value_card": card, "view": "value"}
        # The removed-rows notice describes the WHOLE table; it no longer applies.
        out.pop("removed_rows", None)
        out.pop("removed_notice", None)
    else:
        prow = removed[hits[0]]
        item = str(prow.get("display_label") or prow.get("identifier") or "")
        previous = (prow.get("previous") or {}).get(col.upper())
        note = (f"'{item}' is not reported on {current_date}; its last value, "
                f"on {previous_date}, is shown.")
        card = _card(item=item, measure=measure, column=col, current_date=current_date,
                     previous_date=previous_date, current=None, previous=previous,
                     change=None, pct_change=None, note=note)
        out = {**computed, "rows": [], "removed_rows": [prow], "value_card": card, "view": "value"}
        out.pop("removed_notice", None)

    if card["note"]:
        notes.append(card["note"])
    return out, notes
