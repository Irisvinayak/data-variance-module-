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

from typing import Dict, List, Tuple


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
) -> Tuple[float, bool]:
    """`ranked` is the authorized table shortlist as (table_name, score)
    pairs, already sorted descending, with the QA strong-match bonus
    EXCLUDED from `score` (the caller is responsible for stripping it — see
    retriever.py) so a near-verified QA match doesn't inflate the fused
    number this function normalizes over.

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
    norm_top2 = normalized[ranked[1][0]] if len(ranked) >= 2 else None

    score_gap = 1.0 if norm_top2 is None else (norm_top1 - norm_top2)
    overlap_top1 = lexical_overlaps.get(top1_name, 0.0)

    # top1_share: the winner's share of the TOTAL fused score across the
    # whole shortlist. Unlike min-max normalization (norm_top1 above, used
    # only for score_gap/tie detection), this actually varies: several
    # roughly-equal candidates split the mass and yield a low share (no
    # clear winner -> ask), while one dominant match yields a share near
    # 1.0 (confident -> proceed). Note norm_top1 itself is NOT usable as a
    # confidence signal here -- min-max always maps the top (by definition
    # the max) to exactly 1.0, so it carries no information on its own.
    total_score = sum(score for _, score in ranked)
    if total_score > 1e-9:
        top1_share = ranked[0][1] / total_score
    else:
        top1_share = 1.0 if len(ranked) == 1 else 0.0

    confidence = (
        0.50 * top1_share
        + 0.35 * score_gap
        + 0.15 * overlap_top1
    )
    confidence = max(0.0, min(1.0, confidence))

    # Count candidates within tie_epsilon of the top score (top1 itself
    # always counts), independent of the continuous formula above.
    tied_count = sum(1 for name, _ in ranked if normalized[name] >= norm_top1 - tie_epsilon)
    is_tied = tied_count >= 2

    return confidence, is_tied
