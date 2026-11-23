"""Keep only the rows the user actually asked about.

"Compare Representative Office expenses across all countries" names one line item.
The table holds ~57 rows (every line item x every country), so showing all of them
answers a different question. This module reads the metric phrase left over after the
analyzer removed the return, date and scope, and keeps the rows whose label contains
EVERY remaining content word.

Deliberately conservative — it only narrows when it is sure:
  * a word that is neither generic ("compare", "countries", "latest" ...) nor found in a
    row label means the phrase is about something else (a column, say) -> no filter;
  * no row matches, or every row matches -> no filter (nothing was singled out).
When it does narrow, the caller states what it matched, so it is never silent.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .analysis import _norm

# Words that describe the request, not a row. Anything else must appear in a row label.
_GENERIC = frozenset("""
a an the of for in on at by to from with and or as is are was were be been it its this that
these those show me give get list display tell what which how much many
compare comparison compared comparing variance variation variations change changes changed
increase increased decrease decreased difference differences versus vs against between across
all each every any total overall whole
country countr entry entrie region branch
latest recent current previous prior last past next this quarter quarterly month monthly
year yearly annual period date reporting report as of
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
q1 q2 q3 q4 qoq mom yoy fy cy
""".split())

# Short forms a label may use for a word the user typed in full (and the reverse).
_SYNONYMS = {
    "rep": "representative", "amt": "amount", "no": "number", "nos": "number",
    "govt": "government", "mgmt": "management", "int": "interest",
}


def _canon(token: str) -> str:
    return _SYNONYMS.get(token, token)


def _tokens(text: str) -> list[str]:
    return [_canon(t) for t in _norm(text).split() if t]


def content_words(metric_text: str) -> list[str]:
    """The words of the phrase that must be found in a row label."""
    out: list[str] = []
    for tok in _tokens(metric_text):
        if tok in _GENERIC or tok.isdigit() or len(tok) < 2:
            continue
        # "countries" -> "country" after _norm; guard the singular too
        if tok in ("country", "countr"):
            continue
        if tok not in out:
            out.append(tok)
    return out


def matching_rows(metric_text: Optional[str], rows: list[dict[str, Any]]) -> tuple[Optional[list[int]], list[str]]:
    """(indexes of the rows to keep, or None when no narrowing is justified; the words used)."""
    words = content_words(metric_text or "")
    if not words or not rows:
        return None, words
    keep: list[int] = []
    for i, row in enumerate(rows):
        label_tokens = set(_tokens(str(row.get("display_label") or "")))
        label_tokens |= set(_tokens(str(row.get("identifier") or "")))
        if all(w in label_tokens for w in words):
            keep.append(i)
    if not keep or len(keep) == len(rows):
        return None, words
    return keep, words


def focus_rows(computed: dict[str, Any], metric_text: Optional[str]) -> tuple[dict[str, Any], Optional[str]]:
    """`computed` narrowed to the rows matching the metric phrase, and a note saying so
    (None when nothing was narrowed)."""
    rows = computed.get("rows") or []
    keep, words = matching_rows(metric_text, rows)
    if keep is None:
        return computed, None
    note = (f"Showing the {len(keep)} of {len(rows)} rows matching '{' '.join(words)}'. "
            f"Rephrase to see the rest.")
    return {**computed, "rows": [rows[i] for i in keep]}, note
