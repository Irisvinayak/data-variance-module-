# query_normalizer.py — lightweight typo/synonym correction applied to the
# raw NL query before it's embedded. Cosine similarity over sentence
# embeddings is sensitive to misspelled/informal domain terms (e.g. "exposer"
# for "exposure") nudging the query vector into the wrong neighborhood.
# This is a fixed, hand-maintained dictionary, not a spellchecker — deliberately
# narrow so it only ever corrects known banking-domain terms seen in real
# queries, never touches ordinary words that could change query meaning.

from __future__ import annotations

import re
from typing import Dict

_TERM_MAP: Dict[str, str] = {
    "exposer": "exposure",
    "exposres": "exposures",
    "expsoure": "exposure",
    "npa": "non performing asset",
    "npas": "non performing assets",
    "std": "standard",
    "substd": "sub-standard",
    "sub std": "sub-standard",
    "dom": "domestic",
    "ovs": "overseas",
    "o/s": "outstanding",
    "os": "outstanding",
    "prov": "provision",
    "provn": "provision",
    "rest": "restructured",
    "restr": "restructured",
    "adv": "advances",
    "acc": "account",
    "bal": "balance",
}

# Longest terms first so multi-word entries ("sub std") match before any
# single-word substrings inside them would.
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_TERM_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def normalize_query(query: str) -> str:
    """Return `query` with known typos/abbreviations expanded. Case-preserving
    on everything except the substituted term itself (always lowercased,
    which is fine — embeddings are case-insensitive in practice)."""

    def _sub(match: "re.Match[str]") -> str:
        return _TERM_MAP[match.group(0).lower()]

    return _PATTERN.sub(_sub, query)
