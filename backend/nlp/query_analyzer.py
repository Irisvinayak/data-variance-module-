# query_analyzer.py — one end-to-end pass over the raw user query, run BEFORE
# retrieval, that pulls out every structured fact the query carries and hands
# back the leftover text that actually describes the DATA being asked for.
#
# Why this exists. Until now the entire raw query string was fed straight to
# the embedder, e.g.:
#
#     "show me the variance in total loan assets for CIMS_RAQ quarterly
#      as of 31-Mar-2025 vs last 2 quarters"
#
# Only three of those words ("total loan assets") describe the metric being
# searched for. Everything else is either (a) a fact that belongs to a
# different stage entirely — the return name is an exact lookup, the dates
# belong to date_resolver — or (b) pure boilerplate ("show me the variance
# in"). Embedding all of it drags the query vector away from the metric it
# should be nearest to, and pollutes the BM25 and lexical-overlap signals with
# terms that match every table equally ("variance", "show") or match the wrong
# one (a stray "2025" hitting a table description that happens to cite a year).
#
# So this module splits the query into:
#   - return_ids     : returns the query NAMES outright — an exact match that
#                      should scope retrieval, not compete with it. Matched
#                      ONLY against returns that actually have embeddings
#                      (see indexed_returns.py), because pinning a return with
#                      no vectors produces a column-less shortlist and a
#                      meaningless answer.
#   - date_text      : the date/period phrase, so it can be excluded from the
#                      embedded text. date_resolver still parses the ORIGINAL
#                      query — this module never decides what a date MEANS,
#                      only where one is.
#   - scope          : domestic / overseas / global, which intent_resolver
#                      already uses for _DOM/_OVE column selection.
#   - metric_text    : what's left — the text to actually embed and search.
#
# Nothing here is destructive: the full original query is carried through to
# every downstream stage that wants it. metric_text is an ADDITIONAL, cleaner
# search string, and every extraction falls back to the original when it would
# otherwise leave nothing behind.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ..data.report_lookup import _parse_returns
from . import indexed_returns, schema_info
from .query_normalizer import normalize_query

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


# ── Boilerplate ───────────────────────────────────────────────────────────────
# Phrasing that carries request INTENT ("show me", "what is the variance in")
# rather than content. Stripped from metric_text only — a query that is
# nothing but boilerplate falls back to the original text, so this can never
# empty the search string.
_BOILERPLATE_RE = re.compile(
    r"\b(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    r"(?:show|give|get|fetch|display|list|tell|find|pull|report)\s+"
    r"(?:me\s+|us\s+)?(?:the\s+|a\s+|all\s+)?"
    r"|\b(?:what|which|how\s+much|how\s+many)\s+(?:is|are|was|were)?\s*(?:the\s+)?"
    r"|\b(?:variance|change|movement|difference|comparison|trend|delta)\s+(?:in|of|for|between)\b"
    r"|\bi\s+(?:want|need)\s+(?:to\s+see\s+)?(?:the\s+)?"
    r"|\bdata\s+for\b|\bfigures?\s+for\b|\bvalues?\s+for\b",
    re.IGNORECASE,
)

# ── Date / period phrasing ────────────────────────────────────────────────────
# Deliberately SEPARATE from date_resolver.py's regexes, which are tuned for
# precision (they must not mis-read a bare "2" as a calendar date). These are
# tuned for recall: their only job is to identify a span to EXCLUDE from the
# embedded text, where over-removing a stray date word is harmless and
# under-removing leaves noise in the vector. date_resolver still parses the
# original, unmodified query and remains the single source of truth for what
# any date actually means.
_DATE_PHRASE_RES = [
    # explicit anchors: "as of 31-Mar-2025", "for the period ended March 2025"
    re.compile(
        r"\b(?:as\s+(?:of|on|at)|on|for|dated|ending|ended|period\s+end(?:ed|ing)?)\s+"
        r"(?:the\s+)?(?:period\s+)?"
        r"\d{1,2}[-/\s][a-z]{3,9}[-/\s]\d{2,4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}[-/][a-z]{3,9}[-/]\d{2,4}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"),
    # "since March 2024", "in Jan 2025", bare "March 2025"
    re.compile(
        r"\b(?:since|from|during|in|for|of)?\s*"
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*,?\s*"
        r"(?:'?\d{2,4})\b",
        re.IGNORECASE,
    ),
    # Indian FY-quarter notation, both orders — mirrors date_resolver's
    # _Q_THEN_FY_RE / _FY_THEN_Q_RE.
    re.compile(r"\bQ[1-4](?!\d)(?:\s|,)*(?:of\s+|for\s+)?FY\s*'?\d{2,4}(?:-\d{2,4})?\b", re.IGNORECASE),
    re.compile(r"\bFY\s*'?\d{2,4}(?:-\d{2,4})?(?:\s|,)*(?:of\s+|for\s+)?Q[1-4](?!\d)\b", re.IGNORECASE),
    re.compile(r"\bFY\s*'?\d{2,4}(?:-\d{2,4})?\b", re.IGNORECASE),
    # relative periods — mirrors _extract_relative_periods_back
    re.compile(
        r"\b(?:last|previous|past|trailing)\s+"
        r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)?\s*"
        r"(?:quarters?|qtrs?|months?|mos?|years?|yrs?|periods?|reporting\s+periods?)\b",
        re.IGNORECASE,
    ),
    # finance shorthand — mirrors _XOX_RE
    re.compile(
        r"\b(?:qoq|mom|yoy|wow)\b|"
        r"(?:quarter|month|year|week)[\s-]?over[\s-]?(?:quarter|month|year|week)|"
        r"same\s+(?:period|quarter|month)\s+last\s+year",
        re.IGNORECASE,
    ),
    # bare year, checked last so the richer patterns above claim it first
    re.compile(r"\b(?:19|20)\d{2}\b"),
]

# ── Scope ─────────────────────────────────────────────────────────────────────
# Kept as its own extracted field rather than left implicit in the text:
# intent_resolver already applies _DOM/_OVE column rules from the raw query,
# and surfacing the same decision here means the UI can SHOW the user what was
# understood, and a caller can override it, instead of it being invisible.
_SCOPE_RES = {
    "DOM":    re.compile(r"\bdom(?:estic)?\b|\bindia\b|\bonshore\b", re.IGNORECASE),
    "OVE":    re.compile(r"\bove(?:rseas)?\b|\bforeign\b|\boffshore\b|\boutside\s+india\b", re.IGNORECASE),
    "GLOBAL": re.compile(r"\bglobal\b|\bconsolidated\b|\bcombined\b|\bworldwide\b", re.IGNORECASE),
}

# Reporting-frequency words. Not distinctive enough to identify a return on
# their own (most returns are quarterly), but decisive when the query names a
# return that exists in several frequency variants — e.g. "CIMS_ALE quarterly"
# vs "CIMS_ALE monthly" are different return_ids with different comparison
# periods. Used only to BREAK TIES between returns that already matched.
_FREQ_RES = {
    "D": re.compile(r"\bdaily\b", re.IGNORECASE),
    "W": re.compile(r"\bweekly\b", re.IGNORECASE),
    "F": re.compile(r"\bfortnightly\b", re.IGNORECASE),
    "M": re.compile(r"\bmonthly\b", re.IGNORECASE),
    "Q": re.compile(r"\bquarterly\b|\bquarter\b", re.IGNORECASE),
    "H": re.compile(r"\bhalf[\s-]?yearly\b|\bsemi[\s-]?annual\b", re.IGNORECASE),
    "Y": re.compile(r"\byearly\b|\bannual(?:ly)?\b", re.IGNORECASE),
}

# A token appearing in more than this share of candidate return NAMES carries
# no identifying information in this corpus (e.g. "cims" prefixes essentially
# every return here). Computed from the data rather than hand-listed, so it
# stays correct as returns are added — a hard-coded stopword list would rot
# silently and, worse, would wrongly treat a token as distinctive the moment a
# new family of returns adopted it.
_NON_DISTINCTIVE_DF_RATIO = 0.5

# Words that appear in return names but describe the SCOPE of the data rather
# than identifying the return. A query saying "domestic" is almost always
# scoping a metric ("total staff domestic vs overseas"), not naming
# CIMS_ALE_Domestic — so these can never establish a return match on their own.
# They are still used, once a return has matched on a real identifier, to pick
# between its scope variants (see _narrow_by_name_token).
_SCOPE_NAME_TOKENS = {
    "domestic", "overseas", "oversease", "onshore", "offshore", "foreign",
    "india", "global", "worldwide", "combined",
}

# Always non-distinctive regardless of frequency, since these are structural
# words in return names rather than identifiers.
_STRUCTURAL_NAME_TOKENS = {
    "return", "returns", "report", "reports", "form", "forms", "statement",
    "statements", "data", "summary", "part", "section", "annexure", "annex",
    "daily", "weekly", "fortnightly", "monthly", "quarterly", "halfyearly",
    "yearly", "annual", "annually", "the", "of", "and", "for", "a", "an",
    "standalone", "consolidated",
}


@dataclass
class QueryAnalysis:
    """Everything the query itself states, separated from everything it asks."""

    raw: str
    normalized: str
    metric_text: str
    return_ids: List[str] = field(default_factory=list)
    return_names: List[str] = field(default_factory=list)
    date_text: Optional[str] = None
    has_date_intent: bool = False
    scope: Optional[str] = None
    freq_hint: Optional[str] = None
    # Returns the query NAMES that exist in Returns.xml but have no embeddings.
    # Populated only when the query names no INDEXED return, i.e. the user
    # asked about a real return this pipeline cannot answer. main.py refuses
    # rather than answering, because the alternative — falling through to
    # unscoped retrieval — confidently answers from an unrelated return
    # (measured: "total number of staff for CIMS_ROR" answered from CIMS_RAQ
    # at confidence 0.94).
    unindexed_return_names: List[str] = field(default_factory=list)

    def to_interpretation(self) -> Dict[str, Any]:
        """Client-facing summary of what was understood from the query — so the
        UI can show it back to the user instead of the resolution being a black
        box. Consumed by the `interpretation` field of /variance/nlresolve's
        response (frontend: ControlBar's NlpInterpretation chips)."""
        return {
            "metric_text":   self.metric_text,
            "return_names":  self.return_names,
            "date_text":     self.date_text,
            "scope":         self.scope,
            "freq_hint":     self.freq_hint,
            "unindexed_return_names": self.unindexed_return_names,
        }


def _distinctive_tokens_by_return(
    returns: List[Dict[str, Any]], corpus: List[Dict[str, Any]]
) -> Dict[str, Set[str]]:
    """Per-return set of name tokens that actually identify it.

    Document frequency is counted over `corpus` — EVERY return in Returns.xml —
    not over `returns`, the handful still in the running after the auth and
    embedding-coverage filters. Measuring it on the narrowed list makes
    distinctiveness depend on how many returns happen to have survived
    filtering: with 3 candidates, a token shared by 2 of them clears the
    50%-of-documents bar and gets discarded as "generic", so "ALE" — the single
    most identifying word a user could type here — matched nothing at all. Over
    the real 281-return corpus, "cims" appears in essentially all of them (and
    is correctly dropped) while "ale" appears in a handful (and is correctly
    kept)."""
    doc_freq: Dict[str, int] = {}
    for r in corpus:
        for tok in set(_tokens(r.get("Name", ""))):
            doc_freq[tok] = doc_freq.get(tok, 0) + 1

    cutoff = max(1, int(len(corpus) * _NON_DISTINCTIVE_DF_RATIO))
    return {
        r["Id"]: {
            tok for tok in set(_tokens(r.get("Name", "")))
            if tok not in _STRUCTURAL_NAME_TOKENS
            and tok not in _SCOPE_NAME_TOKENS
            and doc_freq.get(tok, 0) <= cutoff
            # Rarity across 281 return names is NOT enough on its own: over
            # that many names an ordinary financial word is also a rare token
            # of some name, so "gross advances" matched
            # CIMS_CB_Advances_Investments and "debit entries" matched
            # CIMS_DPSS07_Usage_Credit_Debit_Cards. A word the corpus uses to
            # describe its own measures cannot identify a return.
            # See schema_info.looks_like_return_identifier.
            and schema_info.looks_like_return_identifier(tok)
        }
        for r in returns if r.get("Id")
    }


def _match_named_returns(
    query: str, candidates: List[Dict[str, Any]], corpus: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Which of `candidates` does the query name outright?

    Scored by how much of a return's DISTINCTIVE name survives in the query, so
    "CIMS_ALE_Domestic" beats "CIMS_ALE Oversease" for a query mentioning
    domestic, while a bare "ALE" query matches BOTH equally — and returning both
    is the correct outcome: the caller turns a genuine tie into a two-option
    clarification rather than silently picking one (which is how a query for
    domestic data could end up answered from the overseas return).

    Underscores in names ("CIMS_ALE_Domestic") tokenize the same way as spaces,
    so the user need not reproduce the exact punctuation.
    """
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return []

    distinctive = _distinctive_tokens_by_return(candidates, corpus)

    scored = []
    for r in candidates:
        rid = r.get("Id")
        name_tokens = distinctive.get(rid) or set()
        if not name_tokens:
            continue
        matched = name_tokens & query_tokens
        if not matched:
            continue
        # Share of the return's identity present in the query — a query naming
        # 2 of a return's 2 distinctive tokens is a stronger claim than one
        # naming 1 of 3.
        scored.append((len(matched) / len(name_tokens), len(matched), r))

    if not scored:
        return []

    best = max(s[0] for s in scored)
    winners = [s for s in scored if s[0] >= best]
    # Among equal coverage, more absolute matched tokens is the better match.
    best_count = max(s[1] for s in winners)
    return [s[2] for s in winners if s[1] >= best_count]


# Prefixes that mark a return NAME as scoped, mapped to the same scope codes
# _SCOPE_RES produces from the query. Matched by PREFIX rather than exact
# token because the names in Returns.xml are not spelled consistently — the
# overseas ALE return is literally named "CIMS_ALE Oversease(Quarterly)", which
# no exact-match or -anchored rule for "overseas" will ever catch.
_NAME_SCOPE_PREFIXES = (
    ("domestic", "DOM"), ("onshore", "DOM"), ("india", "DOM"),
    ("overs", "OVE"), ("offshore", "OVE"), ("foreign", "OVE"),
    ("global", "GLOBAL"), ("worldwide", "GLOBAL"), ("combined", "GLOBAL"),
)


def _name_scope(name: str) -> Optional[str]:
    for tok in _tokens(name):
        for prefix, code in _NAME_SCOPE_PREFIXES:
            if tok.startswith(prefix):
                return code
    return None


def _narrow_by_scope(matched: List[Dict[str, Any]], scope: Optional[str]) -> List[Dict[str, Any]]:
    """Keep only the matched returns whose NAME carries the scope the query
    asked for.

    Used as a tie-break, never as a match: "domestic" cannot identify a return
    by itself (see _SCOPE_NAME_TOKENS — a query like "total staff domestic vs
    overseas" is scoping a metric, not naming CIMS_ALE_Domestic), but once
    "ALE" has matched both the Domestic and the Oversease variant, that word is
    exactly what tells them apart. A no-op when it would eliminate everything,
    or when none of the matched returns is scope-specific at all."""
    if len(matched) <= 1 or not scope:
        return matched
    if not any(_name_scope(r.get("Name", "")) for r in matched):
        return matched
    narrowed = [r for r in matched if _name_scope(r.get("Name", "")) == scope]
    return narrowed or matched


def _extract_date_text(query: str) -> tuple[str, Optional[str]]:
    """Remove every date/period phrase from `query`, returning
    (query_without_dates, the removed text). Patterns are applied in order and
    each consumes its span, so "as of 31-Mar-2025" is claimed by the anchored
    pattern before the bare-year one can nibble at "2025"."""
    removed: List[str] = []
    remaining = query

    for pattern in _DATE_PHRASE_RES:
        def _capture(match: "re.Match[str]") -> str:
            removed.append(match.group(0).strip())
            return " "
        remaining = pattern.sub(_capture, remaining)

    if not removed:
        return query, None
    return remaining, " ".join(removed)


def _first_match(res: Dict[str, "re.Pattern[str]"], text: str) -> Optional[str]:
    for key, pattern in res.items():
        if pattern.search(text):
            return key
    return None


def _strip_return_mention(text: str, matched_returns: List[Dict[str, Any]]) -> str:
    """Drop the words that named a return from the metric text. The return is
    already pinned by ID at that point, so leaving its name in the embedded
    string only lets it re-compete as a similarity signal — and every table
    under that return embeds the return name, so it matches them all equally
    and discriminates nothing."""
    if not matched_returns:
        return text
    name_tokens: Set[str] = set()
    for r in matched_returns:
        name_tokens.update(t for t in _tokens(r.get("Name", "")) if len(t) > 1)
    if not name_tokens:
        return text
    # Lookarounds on the alphanumeric class, NOT \b: "_" is a word character,
    # so \bCIMS\b does not match inside "CIMS_RAQ" — exactly the punctuation
    # users actually type for these return names, which left the whole name
    # sitting in the embedded metric text.
    pattern = re.compile(
        r"(?<![a-z0-9])(?:"
        + "|".join(re.escape(t) for t in sorted(name_tokens, key=len, reverse=True))
        + r")(?![a-z0-9])",
        re.IGNORECASE,
    )
    return pattern.sub(" ", text)


def _tidy(text: str) -> str:
    """Collapse the whitespace and orphaned connectives/punctuation left behind
    by the removals above ("variance in  for  " -> "variance in")."""
    text = re.sub(r"[_(),]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Looped, not a single pass: removing a return name and a date phrase from
    # the middle of a sentence strands connectives in a RUN, e.g. "...assets
    # for CIMS_RAQ quarterly vs last 2 quarters" collapses to
    # "...assets for vs". One substitution only ever peels the outermost word,
    # which would leave a dangling "for" in the embedded text.
    lead = re.compile(r"^(?:in|of|for|the|a|an|and|to|as|on|at|vs|versus|by|with)\b\s*", re.IGNORECASE)
    trail = re.compile(r"\s*\b(?:in|of|for|the|a|an|and|to|as|on|at|vs|versus|by|with)$", re.IGNORECASE)
    for _ in range(6):  # bounded — each pass strips at most one word per end
        before = text
        text = trail.sub("", lead.sub("", text)).strip(" -–—:,.")
        if text == before:
            break
    return text


def analyze_query(query: str, allowed_return_ids: Optional[Set[str]] = None) -> QueryAnalysis:
    """Full end-to-end read of one user query.

    `allowed_return_ids` scopes the return-name matching to what the caller's
    user may access (main.py passes auth_service.get_allowed_form_ids). It is
    intersected with the embedding coverage set regardless — matching a return
    the index doesn't cover would pin a return whose shortlist has no columns,
    which is exactly the failure this pass exists to prevent.
    """
    raw = (query or "").strip()
    normalized = normalize_query(raw)

    corpus = [r for r in _parse_returns() if r.get("Id") and r.get("Name")]
    candidates = list(corpus)
    if allowed_return_ids is not None:
        candidates = [r for r in candidates if str(r["Id"]) in allowed_return_ids]
    # The coverage filter is NOT optional and NOT caller-controlled: a return
    # without embeddings cannot be answered by this pipeline at all.
    candidates = indexed_returns.filter_returns(candidates)

    matched = _match_named_returns(normalized, candidates, corpus)

    # Did the query name a real return that simply has no embeddings? Checked
    # only when nothing INDEXED matched, so a query naming an answerable return
    # is never second-guessed. Without this the query silently loses its own
    # scope statement and unscoped retrieval answers from whatever scored best
    # anywhere — the worst possible failure, because the confidence is
    # genuinely high and the answer is genuinely about something else.
    unindexed: List[Dict[str, Any]] = []
    if not matched:
        auth_scoped = [r for r in corpus if allowed_return_ids is None
                       or str(r["Id"]) in allowed_return_ids]
        indexed_ids = {str(r["Id"]) for r in candidates}
        not_indexed = [r for r in auth_scoped if str(r["Id"]) not in indexed_ids]
        unindexed = _match_named_returns(normalized, not_indexed, corpus)

    without_dates, date_text = _extract_date_text(normalized)

    # Read the frequency word from the DATE-FREE text: "last 3 quarters" is a
    # comparison span, not a statement that the return is the quarterly
    # variant, and letting it read as one would silently re-point a monthly
    # query at a quarterly return_id.
    freq_hint = _first_match(_FREQ_RES, without_dates)
    if len(matched) > 1 and freq_hint:
        # Same return, several frequency variants ("CIMS_ALE" monthly vs
        # quarterly). Picking the wrong one is not cosmetic — report_freq
        # drives get_previous_dates(), so an annual variant on quarterly data
        # computes comparison periods a YEAR apart and reports wrong numbers
        # with no error. Narrow only when the query says so.
        by_freq = [r for r in matched if (r.get("RepFreq") or "").strip().upper() == freq_hint]
        if by_freq:
            matched = by_freq

    scope = _first_match(_SCOPE_RES, normalized)

    # Scope words break the remaining tie between a return's own scope
    # variants (CIMS_ALE_Domestic vs CIMS_ALE Oversease). Deliberately AFTER
    # the identity match, never as part of it.
    matched = _narrow_by_scope(matched, scope)

    metric_text = _tidy(_BOILERPLATE_RE.sub(" ", _strip_return_mention(without_dates, matched)))
    if not metric_text:
        # The query was entirely return name + date + boilerplate ("CIMS_RAQ
        # for Q1FY25"). There's no metric to search for, but retrieval still
        # needs SOMETHING to embed — fall back to the normalized query rather
        # than an empty vector. The return is pinned by ID anyway, so this
        # path resolves through _shortlist_for_return, not similarity.
        logger.info(
            "[nlp.query_analyzer] query=%r reduced to empty metric text — "
            "falling back to the full normalized query for retrieval", raw,
        )
        metric_text = normalized

    analysis = QueryAnalysis(
        raw=raw,
        normalized=normalized,
        metric_text=metric_text,
        return_ids=[str(r["Id"]) for r in matched],
        return_names=[r["Name"] for r in matched],
        date_text=date_text,
        has_date_intent=date_text is not None,
        scope=scope,
        freq_hint=freq_hint,
        unindexed_return_names=[r["Name"] for r in unindexed],
    )
    logger.info(
        "[nlp.query_analyzer] query=%r -> metric_text=%r | returns=%s | date=%r | scope=%s | "
        "freq=%s%s",
        raw, analysis.metric_text, analysis.return_names, analysis.date_text,
        analysis.scope, analysis.freq_hint,
        f" | names UNINDEXED return(s): {analysis.unindexed_return_names}"
        if analysis.unindexed_return_names else "",
    )
    return analysis
