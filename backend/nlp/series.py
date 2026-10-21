# series.py — multi-period (trend) analysis over a compute_variance result.
#
# compute_variance was called with N real dates (newest first), so every row holds
# its current value plus previous_1 … previous_N. This reads them back as a series
# per (row, column), oldest → newest, and answers the trend questions:
#   "show the trend of X for the last 12 months", "month-on-month variation",
#   "which elements have continuously increased over the last 5 periods",
#   "identify elements with unusual movement over the last six months".
# Like analysis.py it is pure and DB-free, and it never recomputes how variance
# is calculated: the same _pct / _q helpers turn values into changes.

from __future__ import annotations

from decimal import Decimal
from statistics import mean, pstdev
from typing import Any, Optional

from ..data.calculate_variance import _pct, _q
from .analysis import _num, dimension_rows, element_names, match_names
from .operation_parser import AnalysisSpec

INCREASING, DECREASING, FLAT, MIXED = "increasing", "decreasing", "flat", "mixed"


def _z_limit() -> float:
    try:
        from .nlp_config import ANOMALY_Z
        return float(ANOMALY_Z)
    except Exception:
        return 2.0


def _series(row: dict[str, Any], col: str, n_prev: int) -> Optional[list[Optional[Decimal]]]:
    """Values oldest → newest; None where a period has no value."""
    prev = row.get("previous") or {}
    vals: list[Optional[Decimal]] = []
    for k in range(n_prev, 0, -1):
        metric = (prev.get(f"previous_{k}") or {}).get(col)
        vals.append(_num(metric.get("value")) if metric else None)
    vals.append(_num((row.get("current") or {}).get(col.upper())))
    return vals if any(v is not None for v in vals) else None


def _shape(steps: list[Decimal]) -> str:
    if not steps:
        return FLAT
    if all(_q(s) > 0 for s in steps):
        return INCREASING
    if all(_q(s) < 0 for s in steps):
        return DECREASING
    if all(_q(s) == 0 for s in steps):
        return FLAT
    return MIXED


def analyze_series(result: dict[str, Any], spec: AnalysisSpec, dates: list[str]) -> dict[str, Any]:
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    n_prev = max(len(dates) - 1, 0)
    notes = list(spec.notes)
    if n_prev < 1:
        return {**result, "analysis_notes": notes + ["Only one reporting date is available, so there is no trend to show."]}

    wanted = None
    if spec.target_elements:
        hit, missing = match_names(element_names(spec, rows), [
            (i, [r.get("display_label") or "", r.get("identifier") or ""]) for i, r in enumerate(rows)])
        wanted = set(hit)
        notes += [f"Element '{m}' was not found in this table." for m in missing]
    sliced, dim_notes = dimension_rows(rows, spec.dimension_filters)
    notes += dim_notes
    if sliced is not None:
        wanted = sliced if wanted is None else wanted & sliced

    z_limit = _z_limit()
    items: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if wanted is not None and i not in wanted:
            continue
        for col in columns:
            vals = _series(row, col, n_prev)
            if vals is None:
                continue
            known = [v for v in vals if v is not None]
            steps = [b - a for a, b in zip(vals, vals[1:]) if a is not None and b is not None]
            pcts = [_pct(a, b) for a, b in zip(vals, vals[1:]) if a is not None and b is not None]
            unusual: list[int] = []
            if len(steps) >= 3:
                mu, sd = mean(steps), pstdev(steps)
                if sd > 0:
                    unusual = [k + 1 for k, s in enumerate(steps) if abs((s - mu) / sd) > Decimal(str(z_limit))]
            first, last = known[0], known[-1]
            total_pct = _pct(first, last)
            items.append({
                "row": i,
                "identifier": row.get("identifier"),
                "label": row.get("display_label") or row.get("identifier"),
                "column": col,
                "values": [None if v is None else str(_q(v)) for v in vals],
                "changes": [str(_q(s)) for s in steps],
                "pct_changes": [None if p is None else str(_q(p)) for p in pcts],
                "trend": _shape(steps),
                "total_change": str(_q(last - first)),
                "total_pct_change": None if total_pct is None else str(_q(total_pct)),
                "unusual_steps": unusual,
                "missing_periods": sum(v is None for v in vals),
                "_abs_total": abs(last - first),
            })

    picked = items
    streak = spec.streak or spec.direction != "either"
    if streak and spec.direction == "increase":
        picked = [it for it in items if it["trend"] == INCREASING]
    elif streak and spec.direction == "decrease":
        picked = [it for it in items if it["trend"] == DECREASING]
    if spec.unusual:
        picked = [it for it in picked if it["unusual_steps"]]
        notes.append(
            f"Rule: a period-to-period change is unusual when it is more than {z_limit:g} standard deviations "
            "from that item's average change over the window (at least 3 changes are needed).")
    picked.sort(key=lambda it: it["_abs_total"], reverse=True)
    if spec.top_n:
        picked = picked[: spec.top_n]

    keep_rows = {it["row"] for it in picked}
    reduce_rows = wanted is not None or streak or spec.unusual or bool(spec.top_n)
    out_rows = [r for i, r in enumerate(rows) if not reduce_rows or i in keep_rows]
    for it in items:
        it.pop("_abs_total", None)
        it.pop("row", None)

    oldest_first = list(reversed(dates))
    step_word = {"mom": "month-on-month", "qoq": "quarter-on-quarter", "yoy": "year-on-year"}.get(
        spec.granularity or "", "period-on-period")
    counts = {s: sum(it["trend"] == s for it in items) for s in (INCREASING, DECREASING, FLAT, MIXED)}
    if reduce_rows:
        what = ("continuously increasing" if streak and spec.direction == "increase" else
                "continuously decreasing" if streak and spec.direction == "decrease" else
                "with unusual movement" if spec.unusual else "selected")
        summary = (f"{len(picked)} of {len(items)} value series are {what} over {len(dates)} periods "
                   f"({oldest_first[0]} to {oldest_first[-1]}).")
    else:
        summary = (f"{step_word.capitalize()} trend over {len(dates)} periods ({oldest_first[0]} to {oldest_first[-1]}): "
                   f"{counts[INCREASING]} rising throughout, {counts[DECREASING]} falling throughout, "
                   f"{counts[FLAT]} flat, {counts[MIXED]} mixed.")
    if any(it["missing_periods"] for it in picked):
        notes.append("Some series have periods with no value; changes are computed only between periods that have one.")

    return {
        **result,
        "rows": out_rows,
        "summary": summary,
        "analysis_notes": notes,
        "applied_filters": [f"trend over {len(dates)} periods"] + (
            ["continuously " + ("increasing" if spec.direction == "increase" else "decreasing")] if streak and spec.direction != "either" else []),
        "series": {"dates": oldest_first, "step": step_word, "items": picked},
        "analysis": {**spec.to_dict(), "effective_measure": "abs", "matched_rows": len(out_rows),
                     "total_rows": len(rows), "trend_counts": counts},
    }
