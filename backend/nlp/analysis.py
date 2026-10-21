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

import difflib
import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Optional

from ..data.calculate_variance import _pct, _q, _to_decimal
from .operation_parser import AnalysisSpec, Threshold

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


# ── Matching a name the user typed to a real row label / column ───────────────
def _norm(s: str) -> str:
    """'Total Loan-Assets' -> 'total loan asset': lowercase, punctuation out, plural s off."""
    def singular(w: str) -> str:
        if len(w) > 4 and w.endswith("ies"):
            return w[:-3] + "y"
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            return w[:-1]
        return w
    return " ".join(singular(w) for w in re.findall(r"[a-z0-9]+", (s or "").lower()))


def _name_matches(wanted: str, labels: Iterable[str]) -> int:
    """0 = no match; 3 exact, 2 whole-word containment, 1 close spelling."""
    w = _norm(wanted)
    best = 0
    for label in labels:
        lab = _norm(label)
        if not lab or not w:
            continue
        if lab == w:
            return 3
        if re.search(rf"\b{re.escape(w)}\b", lab) or (len(lab) >= 3 and re.search(rf"\b{re.escape(lab)}\b", w)):
            best = max(best, 2)
        elif difflib.SequenceMatcher(None, w, lab).ratio() >= 0.85:
            best = max(best, 1)
    return best


def match_names(wanted: list[str], options: list[tuple[Any, list[str]]]) -> tuple[list[Any], list[str]]:
    """Keys of `options` matching any wanted name (best match level only, per name),
    plus the names that matched nothing. Order follows `options`."""
    keep: set = set()
    missing: list[str] = []
    for name in wanted:
        scored = [(key, _name_matches(name, labels)) for key, labels in options]
        top = max((s for _k, s in scored), default=0)
        if top == 0:
            missing.append(name)
        else:
            keep.update(k for k, s in scored if s == top)
    return [k for k, _l in options if k in keep], missing


def element_names(spec: AnalysisSpec, rows: list[dict[str, Any]]) -> list[str]:
    """The element names to look for: the whole phrase the user typed ("Cash and Cash
    Equivalents") when it is a row label or part of one, else the split names."""
    raw = [r for r in getattr(spec, "target_raw", []) or [] if _norm(r)]
    if raw and spec.target_elements:
        labels = [_norm(str(r.get("display_label") or "")) for r in rows] + [
            _norm(str(r.get("identifier") or "")) for r in rows]
        def hit(name: str) -> bool:
            w = _norm(name)
            return any(l == w or (len(w) >= 4 and re.search(rf"\b{re.escape(w)}\b", l)) for l in labels if l)
        if all(hit(r) for r in raw):
            return raw
    return list(spec.target_elements)


def resolve_target_columns(
    targets: list[str], columns: list[str], label_of: Callable[[str], Optional[str]],
) -> tuple[list[str], list[str]]:
    """Map 'Asset', 'Liabilities' … onto the result's numeric columns by identifier
    or human description. Returns (columns to keep, names that matched nothing)."""
    return match_names(targets, [(c, [c, label_of(c) or ""]) for c in columns])


def dimension_rows(rows: list[dict[str, Any]], filters: list[tuple[str, str]]) -> tuple[Optional[set], list[str]]:
    """Rows whose descriptive (text) values hold every requested value — "Mumbai" in a
    BRANCH column, "retail" in a segment or in the element's own label. The dimension
    word, when given, prefers columns whose name contains it. Returns (row indexes or
    None when there is no filter, notes). Matching is on whole words, case-insensitive."""
    if not filters:
        return None, []
    keep: Optional[set] = None
    notes: list[str] = []
    for dim, value in filters:
        pat = re.compile(rf"(?<![a-z0-9]){re.escape(value.lower())}(?![a-z0-9])")
        stem = (dim or "")[:5].upper()
        hit_dim: set = set()
        hit_any: set = set()
        for i, row in enumerate(rows):
            texts = {k: v for k, v in (row.get("current") or {}).items() if isinstance(v, str)}
            texts["__label__"] = str(row.get("display_label") or "")
            for k, v in texts.items():
                if pat.search(v.lower()):
                    (hit_dim if stem and stem in k.upper() else hit_any).add(i)
        hits = hit_dim or hit_any
        label = f"{dim} '{value}'" if dim else f"'{value}'"
        if not hits:
            notes.append(f"No rows in this table have {label}; nothing to compare for it.")
        elif stem and not hit_dim:
            notes.append(f"No {dim} column holds '{value}'; matched rows that mention it elsewhere.")
        keep = hits if keep is None else keep & hits
    return keep or set(), notes


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


def _record_ok(cell: Cell, spec: AnalysisSpec) -> bool:
    """A limit on the record's own value ("where current value is above ₹1 crore")."""
    t = spec.record_filter
    if t is None:
        return True
    v = cell.prev if t.basis == "previous" else cell.curr
    if t.min_value is not None and not v > t.min_value:
        return False
    if t.max_value is not None and not v < t.max_value:
        return False
    return True


def _anomalies(cells: list[Cell], k: float) -> tuple[list[Cell], Optional[tuple[Decimal, Decimal]]]:
    """Cells whose % change lies outside Tukey's fences (Q1 - k·IQR, Q3 + k·IQR) of
    every defined % change in the comparison. Cells new from zero (undefined %)
    are always listed: going from nothing to something is itself unusual."""
    pcts = sorted(c.pct for c in cells if c.pct is not None)
    if len(pcts) < 4:
        return [c for c in cells if c.status == NEW], None

    def quantile(q: float) -> Decimal:
        pos = (len(pcts) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(pcts) - 1)
        return pcts[lo] + (pcts[hi] - pcts[lo]) * Decimal(str(pos - lo))

    q1, q3 = quantile(0.25), quantile(0.75)
    iqr = q3 - q1
    kk = Decimal(str(k))
    low, high = q1 - kk * iqr, q3 + kk * iqr
    out = [c for c in cells
           if (c.pct is None and c.status == NEW)
           or (c.pct is not None and c.status != UNCHANGED and (c.pct < low or c.pct > high))]
    return out, (low, high)


def _select(cells: list[Cell], spec: AnalysisSpec, which: str) -> list[Cell]:
    cells = [c for c in cells if _record_ok(c, spec)]
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
    if spec.record_filter:
        labels.append(spec.record_filter.describe())
    if spec.top_n:
        labels.append(f"top {spec.top_n} by {'percentage' if which == 'pct' else 'absolute'} change")
    if spec.bottom_n:
        labels.append(f"bottom {spec.bottom_n} by {'percentage' if which == 'pct' else 'absolute'} change")
    if spec.operation == "anomaly":
        labels.append("unusual changes (outside the interquartile fences)")
    if spec.sort_by:
        key = {"name": "element name", "current": "current value", "previous": "previous value",
               "pct": "percentage change", "abs": "absolute change",
               "measure": f"{'percentage' if which == 'pct' else 'absolute'} change"}[spec.sort_by]
        labels.append(f"sorted by {key}, {'highest' if spec.sort_desc else 'lowest'} first"
                      if spec.sort_by != "name" else f"sorted by element name {'Z–A' if spec.sort_desc else 'A–Z'}")
    return labels


FORMULA_TEXT = (
    "Absolute change = current value − previous value. "
    "Percentage change = (current − previous) ÷ |previous| × 100; it is undefined when the previous value is 0."
)


def _top_movers(cells: list[Cell], rows: list[dict[str, Any]], direction: str, which: str, n: int = 5) -> list[str]:
    mine = [c for c in cells if _direction_ok(c, direction) and c.status != UNCHANGED]
    mine.sort(key=lambda c: (c.measure(which) is None, -abs(c.measure(which) or c.change)))
    label = lambda c: rows[c.row_idx].get("display_label") or rows[c.row_idx].get("identifier") or "?"
    return [_fmt_cell(c, label(c), which) for c in mine[:n]]


def _significant_pct() -> Decimal:
    try:
        from .nlp_config import SIGNIFICANT_PCT_DEFAULT
        return Decimal(str(SIGNIFICANT_PCT_DEFAULT)).normalize()
    except Exception:
        return Decimal("10")


def _anomaly_k() -> float:
    try:
        from .nlp_config import ANOMALY_IQR_K
        return float(ANOMALY_IQR_K)
    except Exception:
        return 1.5


def _sort_key(spec: AnalysisSpec, which: str, rows: list[dict[str, Any]]) -> Callable[[Cell], Any]:
    by = spec.sort_by
    if by == "name":
        return lambda c: (rows[c.row_idx].get("display_label") or rows[c.row_idx].get("identifier") or "").lower()
    if by == "current":
        return lambda c: c.curr
    if by == "previous":
        return lambda c: c.prev
    m = which if by == "measure" else by
    # Undefined % (new from zero) sorts last either way.
    if spec.direction == "either":
        return lambda c: abs(c.measure(m)) if c.measure(m) is not None else Decimal("-1")
    return lambda c: c.measure(m) if c.measure(m) is not None else Decimal("-1E30")


def analyze(result: dict[str, Any], spec: AnalysisSpec) -> dict[str, Any]:
    """Return `result` with rows annotated (and, for filter/rank operations, reduced)
    plus `applied_filters`, `summary` and `column_summary`. A result with no
    comparison data is returned unchanged."""
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    cells = _cells(result)
    if not cells:
        return {**result, "analysis_notes": list(spec.notes)} if spec.notes else result

    notes: list[str] = list(spec.notes)

    # "Significant changes" with no number: the configured default threshold,
    # stated in the answer so the user knows what "significant" meant.
    if spec.significant and spec.threshold is None:
        spec = replace(spec, threshold=Threshold(min_value=_significant_pct(), kind="pct"),
                       measure="pct" if spec.measure in ("unspecified", "ambiguous") else spec.measure,
                       operation="filter" if spec.operation == "compare" else spec.operation)
        notes.append(f"'Significant' was read as a change of more than {_significant_pct():f}% in either direction.")

    which = spec.measure if spec.measure in ("pct", "abs") else (
        spec.threshold.kind if spec.threshold and spec.threshold.kind in ("pct", "abs") else "pct")
    if spec.measure == "ambiguous":
        notes.append(f"'Variation' was read as {'percentage' if which == 'pct' else 'absolute'} change.")
    if (spec.threshold and spec.threshold.kind == "abs") or spec.record_filter:
        notes.append("Amounts are compared in the units the report stores its values in.")
    if result.get("truncated"):
        notes.append("The result was truncated at the row limit, so totals and rankings cover only the rows returned.")
    if spec.explain_formula:
        notes.append(FORMULA_TEXT)

    # Duplicate keys: which of the duplicates was compared is arbitrary, so they
    # are never ranked as if the number were reliable.
    dup_ids = set(result.get("duplicate_identifiers") or [])
    dup_rows = {i for i, r in enumerate(rows) if r.get("identifier") in dup_ids} if dup_ids else set()

    # ── a slice by descriptive value ("Mumbai branch", "retail loans") ──
    sliced, dim_notes = dimension_rows(rows, spec.dimension_filters)
    notes += dim_notes
    if sliced is not None:
        cells = [c for c in cells if c.row_idx in sliced]

    # ── named elements: narrow to those rows before anything is counted ──
    targeted = bool(spec.target_elements)
    missing_elements: list[str] = []
    if targeted:
        names = element_names(spec, rows)
        hit, missing_elements = match_names(names, [
            (i, [r.get("display_label") or "", r.get("identifier") or ""]) for i, r in enumerate(rows)])
        for name in missing_elements:
            notes.append(f"Element '{name}' was not found in this table.")
        wanted_rows = set(hit)
        cells = [c for c in cells if c.row_idx in wanted_rows]

    anomaly_fences = None
    if spec.operation == "anomaly":
        chosen, anomaly_fences = _anomalies([c for c in cells if _record_ok(c, spec)
                                             and _direction_ok(c, spec.direction)], _anomaly_k())
        if anomaly_fences is None:
            notes.append("Too few values to judge what is unusual (at least 4 are needed); "
                         "only values that rose from zero are listed.")
        else:
            lo, hi = anomaly_fences
            notes.append(
                f"Rule: a change is unusual when its percentage change is outside "
                f"{_q(lo):,.2f}% to {_q(hi):,.2f}% (quartiles ± {_anomaly_k():g} × interquartile range "
                f"of all changes in this comparison). Values that rose from zero are always listed.")
        chosen.sort(key=lambda c: (c.pct is not None, -abs(c.pct or 0)))
    else:
        chosen = _select(cells, spec, which)
    if spec.operation == "rank" and spec.group_by == "element":
        if dup_rows:
            before = len(chosen)
            chosen = [c for c in chosen if c.row_idx not in dup_rows]
            if len(chosen) < before:
                notes.append("Rows with duplicate keys were left out of the ranking: which duplicate was compared is not reliable.")
        chosen = _rank(chosen, spec, which)

    col_summary = _column_summary(cells, columns)
    if spec.operation == "rank" and spec.group_by == "column":
        keyname = "_chg" if which == "abs" else "_pct"
        usable = [c for c in col_summary if c[keyname] is not None]
        if spec.direction == "increase":
            usable = [c for c in usable if c[keyname] > 0]
            usable.sort(key=lambda c: c[keyname], reverse=not spec.bottom_n)
        elif spec.direction == "decrease":
            usable = [c for c in usable if c[keyname] < 0]
            usable.sort(key=lambda c: c[keyname], reverse=bool(spec.bottom_n))
        else:
            usable.sort(key=lambda c: abs(c[keyname]), reverse=bool(spec.top_n) or not spec.bottom_n)
        col_summary = usable[: spec.top_n or spec.bottom_n or len(usable)]
        if not col_summary:
            notes.append("No column moved in that direction.")
        chosen = [c for c in cells if c.col in {x["column"] for x in col_summary}]
    elif spec.group_by == "column" and spec.operation == "unchanged":
        # "Columns with zero variation": a column counts only when every value in it is unchanged.
        still = {x["column"] for x in col_summary
                 if x["increased"] == 0 and x["decreased"] == 0 and x["new"] == 0 and x["became_zero"] == 0}
        col_summary = [x for x in col_summary if x["column"] in still]
        chosen = [c for c in chosen if c.col in still]
    elif spec.group_by == "column" and spec.operation in ("filter", "new_removed", "anomaly"):
        moved = {c.col for c in chosen}
        col_summary = [x for x in col_summary if x["column"] in moved]

    if spec.sort_by:
        chosen = sorted(chosen, key=_sort_key(spec, which, rows), reverse=spec.sort_desc)
        if spec.sort_by in ("pct", "measure") and which == "pct" and any(c.pct is None for c in chosen):
            notes.append("Values that rose from zero have no percentage change and are listed last.")

    undefined = sum(1 for c in cells if c.pct is None and c.status == NEW)
    if which == "pct" and undefined and (spec.threshold or spec.operation == "rank"):
        notes.append(f"{undefined} value(s) rose from zero; their percentage change is undefined, so they are not ranked by percentage.")

    # ── annotate / reduce rows ──
    by_row: dict[int, list[Cell]] = {}
    for c in cells:
        by_row.setdefault(c.row_idx, []).append(c)
    chosen_ids = {(c.row_idx, c.col) for c in chosen}
    order = {(c.row_idx, c.col): n for n, c in enumerate(chosen)}
    reduce_rows = (targeted or sliced is not None or spec.record_filter is not None
                   or spec.operation in ("filter", "rank", "unchanged", "new_removed", "anomaly"))
    if not reduce_rows and spec.sort_by:
        # Sorting alone keeps every row; order them by their best cell.
        chosen_ids = {(c.row_idx, c.col) for c in cells}
        order = {(c.row_idx, c.col): n for n, c in enumerate(chosen)}

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
        best = min((order[(i, col)] for col in matched if (i, col) in order), default=len(order))
        keyed.append((best, annotated))
    if (spec.operation in ("rank", "anomaly") and spec.group_by == "element") or spec.sort_by:
        keyed.sort(key=lambda kv: kv[0])
    out_rows = [r for _, r in keyed]

    for r in col_summary:
        r.pop("_chg", None)
        r.pop("_pct", None)

    labels = _filter_labels(spec, which)
    if targeted:
        labels.insert(0, "element: " + ", ".join(element_names(spec, rows)))
    for dim, value in spec.dimension_filters:
        labels.insert(0, f"{dim or 'value'}: {value}")
    counts = {s: sum(c.status == s for c in cells) for s in (INCREASED, DECREASED, UNCHANGED, NEW, BECAME_ZERO)}
    label_of = lambda c: rows[c.row_idx].get("display_label") or rows[c.row_idx].get("identifier") or "?"
    if targeted and not cells:
        summary = ("Element " + ", ".join(f"'{n}'" for n in spec.target_elements)
                   + " not found in this table; nothing to compare.")
    elif spec.group_by == "column" and spec.operation in ("rank", "unchanged") :
        names = ", ".join(x["column"] for x in col_summary) or "none"
        summary = f"{len(col_summary)} column(s) match ({'; '.join(labels) or spec.operation}): {names}."
    elif reduce_rows:
        head = f"{len(out_rows)} of {len(rows)} rows match ({'; '.join(labels) or spec.operation})"
        if chosen:
            lead = chosen[0] if spec.operation in ("rank", "anomaly") or spec.sort_by else max(chosen, key=lambda c: abs(c.change))
            head += f". {'First' if spec.operation in ('rank', 'anomaly') or spec.sort_by else 'Largest'}: {_fmt_cell(lead, label_of(lead), which)}"
        else:
            head += ". No values satisfy this condition."
        summary = head + "."
    else:
        summary = (
            f"Compared {len(rows)} rows across {len(columns)} column(s) with the previous period: "
            f"{counts[INCREASED]} increased, {counts[DECREASED]} decreased, {counts[UNCHANGED]} unchanged, "
            f"{counts[NEW]} new from zero, {counts[BECAME_ZERO]} became zero."
        )

    out = {
        **result,
        "rows": out_rows,
        "applied_filters": labels,
        "summary": summary,
        "analysis_notes": notes,
        "column_summary": col_summary,
        "analysis": {**spec.to_dict(), "effective_measure": which,
                     "matched_rows": len(out_rows), "total_rows": len(rows), "counts": counts},
    }
    # Management view: the counts plus the largest movers each way, built only
    # from the computed numbers above.
    if spec.summary or spec.split_directions:
        moved = counts[INCREASED] + counts[DECREASED] + counts[NEW] + counts[BECAME_ZERO]
        out["summary_detail"] = {
            "headline": (f"{moved} of {len(cells)} values changed: "
                         f"{counts[INCREASED] + counts[NEW]} increased, {counts[DECREASED] + counts[BECAME_ZERO]} decreased "
                         f"({counts[NEW]} new from zero, {counts[BECAME_ZERO]} became zero), {counts[UNCHANGED]} unchanged."),
            "top_increases": _top_movers(cells, rows, "increase", which),
            "top_decreases": _top_movers(cells, rows, "decrease", which),
            "measure": which,
        }
    if spec.explain_formula:
        out["formula"] = FORMULA_TEXT
    return out
