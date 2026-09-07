# confidence.py — Table-resolution confidence scoring for the NLP pipeline.
#
# retriever.py's RRF fusion already produces a composite score per candidate
# table, but discards it once the shortlist is built. This module turns that
# (and a couple of cheap side signals) into a single 0-1 confidence number
# plus a tie flag, so backend/main.py can decide whether to resolve
# automatically or ask the user a clarifying question.
#
# Deliberately normalizes WITHIN the current request (top1 vs top2 in this
# shortlist), not against a universal absolute cosine/BM25 threshold — those
# raw scales are already known to be unreliable across queries/corpus size
# (see MIN_TABLE_SCORE/MIN_COLUMN_SCORE's own comments), whereas the relative
# separation between the winner and the runner-up is a stable signal of
# "is this actually ambiguous" regardless of absolute score magnitude.

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .nlp_config import MARGIN_FULL_SEPARATION


def normalize_scores(scores: Dict[str, float]) -> Dict[str, float]:
    """Min-max normalize a {name: score} dict to [0, 1]. A single-entry (or
    empty) dict normalizes to 1.0 for every entry — nothing to compare against,
    so it can't be penalized as ambiguous."""
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    spread = hi - lo
    if spread <= 1e-9:
        return {name: 1.0 for name in scores}
    return {name: (score - lo) / spread for name, score in scores.items()}


def table_confidence(
    ranked: List[Tuple[str, float]],
    qa_hit: bool,
    lexical_overlaps: Dict[str, float],
    tie_epsilon: float,
    signal_leaders: Optional[Set[str]] = None,
) -> Tuple[float, bool]:
    """`ranked` is the authorized table shortlist as (table_name, score)
    pairs, already sorted descending, with the QA strong-match bonus
    EXCLUDED from `score` (the caller is responsible for stripping it — see
    retriever.py) so a near-verified QA match doesn't inflate the fused
    number this function normalizes over.

    `signal_leaders` is the set of tables holding the #1 hit in at least one
    individual retrieval signal (column / row-label / table-description /
    BM25). See retriever.py where it is built, and the rationale below.

    Returns (confidence, is_tied). `is_tied` is a belt-and-suspenders flag,
    independent of the continuous score, for when >=2 candidates land within
    `tie_epsilon` of the top normalized score — callers should treat a tie
    as ambiguous even if the blended confidence number happens to be high.
    """
    if not ranked:
        return 0.0, False

    top1_name = ranked[0][0]
    if qa_hit:
        # A near-verified QA-pairs match should never be second-guessed by
        # the weaker retrieval signals below.
        return 1.0, False

    normalized = normalize_scores(dict(ranked))
    norm_top1 = normalized[top1_name]

    # ── Signal 1: relative margin over the runner-up ────────────────────────
    # Scale-free: (top1 - top2) / top1 asks "how much better is the winner
    # than the next best", which is meaningful regardless of the absolute
    # fused magnitude. Saturating at MARGIN_FULL_SEPARATION because real
    # margins are small even for clear winners (measured mean 0.18 on correct
    # answers over the benchmark in nlp_config.RRF_K) — without the scaling
    # this term would contribute a rounding error.
    top1_score = ranked[0][1]
    if len(ranked) < 2 or top1_score <= 1e-9:
        margin = 1.0
    else:
        margin = max(0.0, (top1_score - ranked[1][1]) / top1_score)
    margin_term = min(1.0, margin / MARGIN_FULL_SEPARATION) if MARGIN_FULL_SEPARATION > 0 else 0.0

    # ── Signal 2: does the winner OWN a signal? ─────────────────────────────
    # The strongest predictor available, measured over the benchmark:
    #   leads >=1 signal -> 81% correct (n=57);  leads none -> 0% (n=3).
    # A table that never ranked first in ANY signal won the fused sum purely
    # by accumulating mediocre placements across several of them, which
    # measurement showed is a NEGATIVE indicator (present in 4 signals: 62%
    # correct, vs 3 signals: 90%) — generic date/code/"total" tables surface
    # everywhere without ever being the best answer to anything. Weighted
    # heavily, and its absence is what keeps such a candidate below the ask
    # floor instead of auto-proceeding on a wrong table.
    # None means the caller didn't compute this (it is an optional argument, so
    # a future/other caller can omit it) -> treated as NEUTRAL rather than as
    # "leads nothing". Scoring an unknown the same as a known-absent would
    # silently cap such a caller at 0.55 and make auto-proceed unreachable for
    # them — which is the exact failure this rewrite exists to remove. An empty
    # SET is different: it means the caller looked and this table leads
    # nothing, which is the strongly negative case.
    if signal_leaders is None:
        leader_term = 0.5
    else:
        leader_term = 1.0 if top1_name in signal_leaders else 0.0

    overlap_top1 = lexical_overlaps.get(top1_name, 0.0)

    # Replaces the previous 0.50*top1_share term, which was structurally
    # incapable of doing its job: top1_share is the winner's fraction of the
    # summed shortlist score, and because RRF (at any k) produces similar
    # magnitudes across the top-K, that fraction sits near 1/K almost
    # regardless of how good the match is. With K=5 the whole formula could
    # not exceed ~0.65 in the typical case against a 0.72 auto-proceed
    # threshold, so NO query ever auto-proceeded — every single one fell
    # through to a clarifying question (confirmed: 0/60 on the benchmark, and
    # in the production logs). It also rewarded the same breadth pathology
    # described above, since a table scored by more signals has a larger
    # share.
    confidence = (
        0.45 * leader_term
        + 0.40 * margin_term
        + 0.15 * overlap_top1
    )
    confidence = max(0.0, min(1.0, confidence))

    # Count candidates within tie_epsilon of the top score (top1 itself
    # always counts), independent of the continuous formula above.
    tied_count = sum(1 for name, _ in ranked if normalized[name] >= norm_top1 - tie_epsilon)
    is_tied = tied_count >= 2

    return confidence, is_tied
