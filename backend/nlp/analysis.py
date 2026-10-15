# analysis.py — apply an AnalysisSpec to a computed variance result.
#
# Runs AFTER service.compute_variance and never touches how variance is calculated:
# it re-reads the raw current/previous values from each row (not the formatted
# "12.50%" strings), classifies every (row, column) cell, then filters, ranks and
# summarises. Pure functions over the compute result, so it is DB-free and testable.
#
# Only the FIRST comparison period (previous_1) is analysed; multi-period work
# (trends, streaks) goes through the separate series path.

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from ..data.calculate_variance import _pct, _q, _to_decimal
from .operation_parser import AnalysisSpec

_ZERO = Decimal("0")

# Cell statuses
UNCHANGED, INCREASED, DECREASED, NEW, BECAME_ZERO = (
    "unchanged", "increased", "decreased", "new", "became_zero")


@dataclass
class Cell:
    row_idx: int
    col: str
    prev: Decimal
    curr: Decimal
    change: Decimal
    pct: Optional[Decimal]          # None: undefined (zero base)
    status: str

    def measure(self, which: str) -> Optional[Decimal]:
        return self.pct if which == "pct" else self.change


def _classify(prev: Decimal, curr: Decimal) -> str:
    if _q(curr - prev) == 0:
        return UNCHANGED
    if prev == 0:
        return NEW
    if curr == 0:
        return BECAME_ZERO
    return INCREASED if curr > prev else DECREASED


def _num(v: Any) -> Optional[Decimal]:
    try:
        return _to_decimal(v)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _cells(result: dict[str, Any]) -> list[Cell]:
    cells: list[Cell] = []
    columns = result.get("columns") or []
    for i, row in enumerate(result.get("rows") or []):
        prev_block = (row.get("previous") or {}).get("previous_1")
        if not prev_block:
            continue
        current = row.get("current") or {}
        for col in columns:
            metric = prev_block.get(col)
            if not metric:
                continue
            p, c = _num(metric.get("value")), _num(current.get(col.upper()))
            if p is None or c is None:
                continue
            cells.append(Cell(i, col, p, c, c - p, _pct(p, c), _classify(p, c)))
    return cells


def _direction_ok(cell: Cell, direction: str) -> bool:
    if direction == "either":
        return True
    if direction == "increase":
        return cell.status in (INCREASED, NEW)
    return cell.status in (DECREASED, BECAME_ZERO)


def _threshold_ok(cell: Cell, spec: AnalysisSpec, which: str) -> bool:
    t = spec.threshold
    if t is None:
        return True
    m = cell.measure(which)
    if m is None:                       # percent is undefined from a zero base
        return False
    v = m if t.signed else abs(m)
    both = t.min_value is not None and t.max_value is not None
    if t.min_value is not None and not (v >= t.min_value if both else v > t.min_value):
        return False
    if t.max_value is not None and not (v <= t.max_value if both else v < t.max_value):
        return False
    return True


def _select(cells: list[Cell], spec: AnalysisSpec, which: str) -> list[Cell]:
    if spec.operation == "unchanged":
        return [c for c in cells if c.status == UNCHANGED]
    if spec.operation == "new_removed":
        wanted = {"new": {NEW}, "zero": {BECAME_ZERO}, "both": {NEW, BECAME_ZERO}}[spec.new_removed or "both"]
        return [c for c in cells if c.status in wanted]
    picked = [c for c in cells if _direction_ok(c, spec.direction) and _threshold_ok(c, spec, which)]
    if spec.operation == "filter":
        # A filter on direction/threshold is about movement, not stillness.
        return [c for c in picked if c.status != UNCHANGED]
    return picked


def _rank(cells: list[Cell], spec: AnalysisSpec, which: str) -> list[Cell]:
    ranked = [c for c in cells if c.measure(which) is not None]
    n = spec.top_n or spec.bottom_n or len(ranked)
    if spec.direction == "increase":
        key, rev = (lambda c: c.measure(which)), bool(spec.top_n)
    elif spec.direction == "decrease":
        key, rev = (lambda c: c.measure(which)), not spec.top_n
    else:
        key, rev = (lambda c: abs(c.measure(which))), bool(spec.top_n) or not spec.bottom_n
    return sorted(ranked, key=key, reverse=rev)[:n]


def _column_summary(cells: list[Cell], columns: list[str]) -> list[dict[str, Any]]:
    out = []
    for col in columns:
        mine = [c for c in cells if c.col == col]
        if not mine:
            continue
        prev, curr = sum((c.prev for c in mine), _ZERO), sum((c.curr for c in mine), _ZERO)
        pct = _pct(prev, curr)
        out.append({
            "column": col,
            "total_previous": str(_q(prev)), "total_current": str(_q(curr)),
            "change": str(_q(curr - prev)), "pct_change": None if pct is None else str(_q(pct)),
            "increased": sum(c.status in (INCREASED, NEW) for c in mine),
            "decreased": sum(c.status in (DECREASED, BECAME_ZERO) for c in mine),
            "unchanged": sum(c.status == UNCHANGED for c in mine),
            "new": sum(c.status == NEW for c in mine),
            "became_zero": sum(c.status == BECAME_ZERO for c in mine),
            "_chg": curr - prev, "_pct": pct,
        })
    return out


def _fmt_cell(cell: Cell, label: str, which: str) -> str:
    val = f"{_q(cell.change):+,.2f}" if which == "abs" else (
        "new from zero" if cell.pct is None else f"{_q(cell.pct):+,.2f}%")
    return f"{label} / {cell.col}: {val} ({cell.prev:,.2f} → {cell.curr:,.2f})"


def _filter_labels(spec: AnalysisSpec, which: str) -> list[str]:
    labels: list[str] = []
    if spec.operation == "unchanged":
        labels.append("unchanged values")
    elif spec.operation == "new_removed":
        labels.append({"new": "newly populated (previous was zero)",
                       "zero": "became zero", "both": "newly populated or became zero"}[spec.new_removed or "both"])
    if spec.direction != "either":
        labels.append("increases only" if spec.direction == "increase" else "decreases only")
    if spec.threshold:
        labels.append(f"{'percentage' if which == 'pct' else 'absolute'} change {spec.threshold.describe()}")
    if spec.top_n:
        labels.append(f"top {spec.top_n} by {'percentage' if which == 'pct' else 'absolute'} change")
    if spec.bottom_n:
        labels.append(f"bottom {spec.bottom_n} by {'percentage' if which == 'pct' else 'absolute'} change")
    return labels


def analyze(result: dict[str, Any], spec: AnalysisSpec) -> dict[str, Any]:
    """Return `result` with rows annotated (and, for filter/rank operations, reduced)
    plus `applied_filters`, `summary` and `column_summary`. A result with no
    comparison data is returned unchanged."""
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    cells = _cells(result)
    if not cells:
        return result

    notes: list[str] = list(spec.notes)
    which = spec.measure if spec.measure in ("pct", "abs") else (
        spec.threshold.kind if spec.threshold and spec.threshold.kind in ("pct", "abs") else "pct")
    if spec.measure == "ambiguous":
        notes.append(f"'Variation' was read as {'percentage' if which == 'pct' else 'absolute'} change.")
    if spec.threshold and spec.threshold.kind == "abs":
        notes.append("Amounts are compared in the units the report stores its values in.")
    if result.get("truncated"):
        notes.append("The result was truncated at the row limit, so totals and rankings cover only the rows returned.")

    chosen = _select(cells, spec, which)
    if spec.operation == "rank" and spec.group_by == "element":
        chosen = _rank(chosen, spec, which)

    col_summary = _column_summary(cells, columns)
    if spec.operation == "rank" and spec.group_by == "column":
        keyname = "_chg" if which == "abs" else "_pct"
        usable = [c for c in col_summary if c[keyname] is not None]
        usable.sort(key=lambda c: abs(c[keyname]), reverse=bool(spec.top_n) or not spec.bottom_n)
        col_summary = usable[: spec.top_n or spec.bottom_n or len(usable)] or col_summary
        chosen = [c for c in cells if c.col in {x["column"] for x in col_summary}]

    undefined = sum(1 for c in cells if c.pct is None and c.status == NEW)
    if which == "pct" and undefined and (spec.threshold or spec.operation == "rank"):
        notes.append(f"{undefined} value(s) rose from zero; their percentage change is undefined, so they are not ranked by percentage.")

    # ── annotate / reduce rows ──
    by_row: dict[int, list[Cell]] = {}
    for c in cells:
        by_row.setdefault(c.row_idx, []).append(c)
    chosen_ids = {(c.row_idx, c.col) for c in chosen}
    order = {(c.row_idx, c.col): n for n, c in enumerate(chosen)}
    reduce_rows = spec.operation in ("filter", "rank", "unchanged", "new_removed")

    keyed: list[tuple[int, dict[str, Any]]] = []   # (best rank of the row's matched cells, row)
    for i, row in enumerate(rows):
        mine = by_row.get(i, [])
        matched = [c.col for c in mine if (c.row_idx, c.col) in chosen_ids]
        if reduce_rows and not matched:
            continue
        annotated = dict(row)
        annotated["analysis"] = {
            "matched_columns": matched,
            "cells": {c.col: {
                "status": c.status, "change": str(_q(c.change)),
                "pct_change": None if c.pct is None else str(_q(c.pct)),
                "matched": (c.row_idx, c.col) in chosen_ids,
            } for c in mine},
        }
        best = min((order[(i, col)] for col in matched), default=len(order))
        keyed.append((best, annotated))
    if spec.operation == "rank" and spec.group_by == "element":
        keyed.sort(key=lambda kv: kv[0])
    out_rows = [r for _, r in keyed]

    for r in col_summary:
        r.pop("_chg", None)
        r.pop("_pct", None)

    labels = _filter_labels(spec, which)
    counts = {s: sum(c.status == s for c in cells) for s in (INCREASED, DECREASED, UNCHANGED, NEW, BECAME_ZERO)}
    if reduce_rows:
        head = f"{len(out_rows)} of {len(rows)} rows match ({'; '.join(labels) or spec.operation})"
        if chosen:
            label_of = lambda c: rows[c.row_idx].get("display_label") or rows[c.row_idx].get("identifier") or "?"
            lead = chosen[0] if spec.operation == "rank" else max(chosen, key=lambda c: abs(c.change))
            head += f". {'First' if spec.operation == 'rank' else 'Largest'}: {_fmt_cell(lead, label_of(lead), which)}"
        elif not chosen:
            head += ". No values satisfy this condition."
        summary = head + "."
    else:
        summary = (
            f"Compared {len(rows)} rows across {len(columns)} column(s) with the previous period: "
            f"{counts[INCREASED]} increased, {counts[DECREASED]} decreased, {counts[UNCHANGED]} unchanged, "
            f"{counts[NEW]} new from zero, {counts[BECAME_ZERO]} became zero."
        )

    return {
        **result,
        "rows": out_rows,
        "applied_filters": labels,
        "summary": summary,
        "analysis_notes": notes,
        "column_summary": col_summary,
        "analysis": {**spec.to_dict(), "effective_measure": which,
                     "matched_rows": len(out_rows), "total_rows": len(rows), "counts": counts},
    }
