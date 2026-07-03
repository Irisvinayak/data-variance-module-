# ========================= calculate_variance.py =========================
# Standalone copy of the variance calculation logic.
# All imports are stdlib / third-party only — no chatbot dependencies.

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from dateutil.relativedelta import relativedelta
from typing import Callable, List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# ── SR-NO / serial-number column exclusion ────────────────────────────────────
_EXCLUDED_VALUE_COLS = {
    "SRNO", "SR_NO", "SR-NO", "SR.NO",
    "SLNO", "SL_NO", "SLNO.",
    "SNO",  "S_NO",
    "ROWNUM", "ROW_NUM",
}


def _is_excluded_value_col(col: str) -> bool:
    """Return True if `col` is a serial-number-like column that should never
    be treated as a value column for variance computation."""
    stripped = re.sub(r"[^A-Z]", "", col.upper())
    excluded_stripped = {re.sub(r"[^A-Z]", "", x) for x in _EXCLUDED_VALUE_COLS}
    return stripped in excluded_stripped


# ── Row display-label hint words ──────────────────────────────────────────────
_LABEL_HINT_WORDS = (
    "NAME", "DESC", "DESCRIPTION",
    "PARTICULAR", "PARTICULARS",
    "ITEM", "LABEL", "TITLE",
    "HEAD", "HEADING", "CATEGORY", "CAT",
    "SCHEDULE", "SCH", "COMPONENT", "DETAIL",
    "NARRATION", "NARATION", "REMARK", "REMARKS",
    "TEXT", "CAPTION", "FIELD",
)


def _label_score(col: str, rows: List[Dict]) -> float:
    """
    Score how likely `col` contains human-readable description text (0–1).

    Criteria (each contributes to the score):
    - Average word count > 1  → likely a phrase, not a code
    - Average string length > 8 → longer values suggest descriptions
    - Fraction of values containing a space → spaced text
    - Low fraction of purely-numeric or all-caps-short tokens → not a code column
    - High fraction of mixed-case values → typed descriptions
    """
    values = []
    for row in rows:
        v = row.get(col)
        if v is not None:
            s = str(v).strip()
            if s:
                values.append(s)

    if not values:
        return 0.0

    n = len(values)

    # Average length
    avg_len = sum(len(v) for v in values) / n

    # Fraction with at least one space (multi-word)
    frac_spaces = sum(1 for v in values if " " in v) / n

    # Average word count
    avg_words = sum(len(v.split()) for v in values) / n

    # Fraction that are purely numeric (bad for labels)
    frac_numeric = sum(1 for v in values if v.replace(",", "").replace(".", "").replace("-", "").isdigit()) / n

    # Fraction that look like short codes (≤6 chars, all upper / alphanumeric only)
    frac_code_like = sum(
        1 for v in values
        if len(v) <= 6 and re.match(r'^[A-Z0-9_\-\.]+$', v)
    ) / n

    score = (
        min(avg_len / 30.0, 1.0) * 0.30       # length contribution (cap at 30 chars)
        + frac_spaces * 0.35                   # spaces = descriptive text
        + min(avg_words / 3.0, 1.0) * 0.20    # word count contribution
        - frac_numeric * 0.40                  # penalise numeric-looking columns
        - frac_code_like * 0.30                # penalise code-like short tokens
    )
    return max(score, 0.0)


def _pick_label_columns(all_cols: List[str], comp_cols: List[str], rows: Optional[List[Dict]] = None) -> List[str]:
    """
    Pick the best column(s) to use as a human-readable row label.

    Strategy (in priority order):
    1. Columns whose name contains a known label hint word.
    2. If nothing matched by name, score every non-comp, non-numeric text column
       by how description-like its actual values are, and pick the highest-scoring
       one (if its score is above a minimum threshold).
    3. Fall back to comp_cols so there is always *something* to show.
    """
    upper_comp = {c.upper() for c in comp_cols}

    # ── Pass 1: name-hint match ───────────────────────────────────────────────
    hinted = [
        c for c in all_cols
        if c.upper() not in upper_comp
        and any(h in c.upper() for h in _LABEL_HINT_WORDS)
    ]
    if hinted:
        return hinted

    # ── Pass 2: data-driven scoring ──────────────────────────────────────────
    if rows:
        candidates = [
            c for c in all_cols
            if c.upper() not in upper_comp
            and not _is_excluded_value_col(c)
            and not _is_numeric_col(c, rows)
        ]
        if candidates:
            scored = [(c, _label_score(c, rows)) for c in candidates]
            scored.sort(key=lambda x: x[1], reverse=True)
            best_col, best_score = scored[0]
            logger.info(
                "[variance] Label column data-score results: %s",
                [(c, round(s, 3)) for c, s in scored],
            )
            if best_score >= 0.15:   # minimum threshold — anything above this is plausibly descriptive
                logger.info(
                    "[variance] Auto-selected label column by data scoring: %r (score=%.3f)",
                    best_col, best_score,
                )
                return [best_col]

    # ── Pass 3: fallback to identifier columns ────────────────────────────────
    return list(comp_cols)


# ── Canonical frequency-code groups ──────────────────────────────────────────

_MONTHLY      = {"M", "MONTHLY"}
_QUARTERLY    = {"Q", "QUARTERLY"}

_HY_FIN       = {"H", "HALFYEARLY", "HY", "FH"}
_HY_CAL       = {"C", "CH"}

_ANNUAL_FIN   = {"A", "ANNUAL", "Y", "FY"}
_ANNUAL_CAL   = {"B", "CY"}

_WEEKLY       = {"W", "WEEKLY"}
_FORTNIGHTLY  = {"F", "FORTNIGHTLY", "HM"}

_DAILY        = {"D", "DAILY", "G"}


def get_previous_dates(
    current_date: datetime,
    report_freq: str,
    periods: int,
) -> List[datetime]:

    logger.info(
        "[variance] Calculating previous dates | current=%s | freq=%s | periods=%s",
        current_date, report_freq, periods,
    )

    dates: List[datetime] = []
    prev = current_date
    freq = report_freq.strip().upper()

    for _ in range(periods):

        if freq in _MONTHLY:
            prev = prev - relativedelta(months=1)

        elif freq in _QUARTERLY:
            prev = prev - relativedelta(months=3)

        elif freq in _HY_FIN or freq in _HY_CAL:
            prev = prev - relativedelta(months=6)

        elif freq in _ANNUAL_FIN or freq in _ANNUAL_CAL:
            prev = prev - relativedelta(years=1)

        elif freq in _WEEKLY:
            prev = prev - relativedelta(weeks=1)
            dates.append(prev)
            continue

        elif freq in _FORTNIGHTLY:
            if prev.day == 15:
                prev = prev.replace(day=1) - relativedelta(days=1)
            else:
                prev = prev.replace(day=15)
            dates.append(prev)
            continue

        elif freq in _DAILY:
            prev = prev - relativedelta(days=1)
            dates.append(prev)
            continue

        else:
            logger.warning("[variance] Unknown frequency=%s defaulting to monthly", freq)
            prev = prev - relativedelta(months=1)

        last_day = calendar.monthrange(prev.year, prev.month)[1]
        prev = prev.replace(day=last_day)
        dates.append(prev)

    return dates


def validate_reporting_date(report_date: datetime, report_freq: str) -> bool:
    freq     = report_freq.strip().upper()
    day      = report_date.day
    month    = report_date.month
    year     = report_date.year
    last_day = calendar.monthrange(year, month)[1]

    if freq in _WEEKLY:
        return report_date.weekday() == 4
    if freq in _FORTNIGHTLY:
        return day in (15, last_day)
    if freq in _DAILY:
        return True
    if day != last_day:
        return False
    if freq in _MONTHLY:
        return True
    if freq in _QUARTERLY:
        return month in (3, 6, 9, 12)
    if freq in _HY_FIN:
        return month in (3, 9)
    if freq in _HY_CAL:
        return month in (6, 12)
    if freq in _ANNUAL_FIN:
        return month == 3
    if freq in _ANNUAL_CAL:
        return month == 12
    return True


def _to_decimal(v: Any) -> Decimal:
    if v is None:
        raise InvalidOperation("None")
    return Decimal(str(v).replace(",", ""))


def get_difference(prev_val: Any, curr_val: Any) -> Optional[Dict[str, Any]]:
    try:
        p = _to_decimal(prev_val)
        c = _to_decimal(curr_val)
        diff  = (c - p).quantize(Decimal("0.01"))
        color = "danger" if c < p else ("success" if c > p else "")
        return {"value": str(diff), "color": color}
    except Exception:
        try:
            if str(prev_val).strip().lower() != str(curr_val).strip().lower():
                return {"value": str(curr_val), "color": ""}
            # Equal string values — return zero-change dict rather than None
            return {"value": "0.00", "color": ""}
        except Exception:
            return None


def get_pct_change(prev_val: Any, curr_val: Any) -> Optional[Dict[str, Any]]:
    try:
        p       = _to_decimal(prev_val)
        c       = _to_decimal(curr_val)
        pct     = ((c - p) / abs(p)) * Decimal("100") if p != 0 else Decimal("0")
        rounded = pct.quantize(Decimal("0.01"))
        color   = "danger" if c < p else ("success" if c > p else "")
        return {"value": f"{rounded}%", "color": color}
    except Exception:
        return None


def get_variance_summary(prev_val: Any, curr_val: Any) -> Optional[Dict[str, Any]]:
    try:
        p           = _to_decimal(prev_val)
        c           = _to_decimal(curr_val)
        pct         = ((c - p) / abs(p)) * Decimal("100") if p != 0 else Decimal("0")
        rounded_pct = pct.quantize(Decimal("0.01"))
        arrow       = "▲" if c > p else ("▼" if c < p else "")
        color       = "success" if c > p else ("danger" if c < p else "")
        return {
            "text": f"{c:,.2f} {arrow} {rounded_pct:+.2f}% (Prev: {p:,.2f})",
            "arrow": arrow,
            "color": color,
        }
    except Exception:
        return None


def build_identifier(row: Dict[str, Any], comp_filter_cols: List[str]) -> str:
    """Build a composite business-key string from the given columns."""
    row_upper = {k.upper(): v for k, v in row.items()}
    parts = [
        str(row_upper.get(col.upper(), "")).strip()
        for col in comp_filter_cols
    ]
    return "_".join(p for p in parts if p)


def build_query(
    table_name: str,
    metadata: Dict[str, Any],
    current_date: datetime,
    prev_dates: List[datetime],
    return_code: Any,
    selected_columns: Optional[List[str]] = None,
) -> str:

    fc         = metadata["filter_col"]
    all_dates  = [current_date] + prev_dates
    conditions = [
        f"{fc} = TO_DATE('{d.strftime('%d-%b-%Y').upper()}', 'DD-MON-YYYY')"
        for d in all_dates
    ]
    date_sql = " OR ".join(conditions)

    rc_filter = (
        f" AND {metadata.get('return_code_col')} = '{return_code}'"
        if metadata.get("is_single")
        else ""
    )
    freq_filter = (
        f" AND {metadata.get('freq_col')} = '{metadata.get('freq_val')}'"
        if metadata.get("freq_col") and metadata.get("freq_val")
        else ""
    )

    cols  = ", ".join(selected_columns) if selected_columns else "*"
    query = (
        f"SELECT {cols} FROM {table_name} "
        f"WHERE ({date_sql}){rc_filter}{freq_filter}"
    )
    logger.info("[variance] Generated Query:\n%s", query)
    return query


def _parse_date_like(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y",
        "%d-%m-%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    for fmt in ("%d-%b-%y", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    logger.error("[variance] *** COULD NOT PARSE DATE value=%r", value)
    return None


def dates_match(value: Any, target: datetime) -> bool:
    d = _parse_date_like(value)
    if d is None:
        return False
    return d.year == target.year and d.month == target.month and d.day == target.day


def _normalize_row_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k.upper(): v for k, v in row.items()}


def _is_numeric_col(col: str, rows: List[Dict]) -> bool:
    """Return True if at least one row has a parseable numeric value for col."""
    for row in rows:
        v = row.get(col)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        try:
            Decimal(s.replace(",", ""))
            return True
        except Exception:
            pass
    return False


def calculate_variance(
    return_code: Any,
    table_name: str,
    reporting_date: str,
    get_table_metadata_fn: Callable[..., Dict[str, Any]],
    execute_query_fn: Callable[..., List[Dict[str, Any]]],
    connection_string: Optional[str] = None,
    is_non_xbrl: bool = False,
    reporting_period: int = 1,
    selected_columns: Optional[List[str]] = None,
    comparison_mode: str = "vs_current",
) -> Dict[str, Any]:

    logger.info(
        "[variance] START | table=%s | date=%s | periods=%s",
        table_name, reporting_date, reporting_period,
    )

    try:
        rdate = datetime.strptime(reporting_date.upper(), "%d-%b-%Y")
    except Exception as exc:
        return {"error": f"Invalid reporting date format: {exc}"}

    metadata    = get_table_metadata_fn(return_code, table_name, is_non_xbrl)
    report_freq = metadata.get("report_freq", "M")

    if not validate_reporting_date(rdate, report_freq):
        return {"error": "Invalid Reporting Date According To Frequency."}

    prev_dates = get_previous_dates(rdate, report_freq, reporting_period)

    query = build_query(
        table_name, metadata, rdate, prev_dates, return_code, selected_columns
    )

    try:
        if connection_string is not None:
            all_rows = execute_query_fn(query, connection_string)
        else:
            all_rows = execute_query_fn(query)
    except Exception as exc:
        return {"error": str(exc)}

    if not all_rows:
        return {"error": f"No data found for {table_name} on {reporting_date}"}

    all_rows = [_normalize_row_keys(r) for r in all_rows]
    fc       = metadata["filter_col"].upper()

    current_rows = [r for r in all_rows if dates_match(r.get(fc), rdate)]

    if not current_rows:
        distinct_dates = sorted({str(r.get(fc)) for r in all_rows})
        return {
            "error": (
                f"No data found for {table_name} on {reporting_date}. "
                f"Dates available in DB: {', '.join(distinct_dates)}"
            )
        }

    comp_cols = [c.upper() for c in (metadata.get("comp_filter_col_names") or [])]

    # All columns to display (exclude date/filter col and identifier/comp cols)
    all_display_cols = [
        k for k in all_rows[0].keys()
        if k.upper() != fc and k.upper() not in set(comp_cols)
    ]

    if selected_columns is None:
        # Only numeric, non-code, non-serial-number columns get variance computed
        selected_columns = [
            c for c in all_display_cols
            if "CODE" not in c.upper()
            and not _is_excluded_value_col(c)
            and _is_numeric_col(c, all_rows)
        ]
        logger.info(
            "[variance] Numeric value columns selected for comparison (%d): %s",
            len(selected_columns), selected_columns,
        )
    else:
        # Caller-supplied list — strip excluded columns server-side
        filtered_explicit = [
            c for c in selected_columns
            if not _is_excluded_value_col(c)
        ]
        stripped = [c for c in selected_columns if _is_excluded_value_col(c)]
        if stripped:
            logger.warning(
                "[variance] Stripped %d excluded column(s) from caller-supplied "
                "selected_columns: %s",
                len(stripped), stripped,
            )
        selected_columns = filtered_explicit

    # ── Auto-detect identifier columns when CompFilterColName is absent in XML ──
    if not comp_cols:
        numeric_set = {c.upper() for c in (selected_columns or [])}
        auto_id = [
            k for k in all_rows[0].keys()
            if k.upper() != fc
            and k.upper() not in numeric_set
            and not _is_numeric_col(k, all_rows)
        ]
        # Last resort: any non-date column
        if not auto_id:
            auto_id = [k for k in all_rows[0].keys() if k.upper() != fc]
        if not auto_id:
            return {"error": (
                f"Cannot build row identifier for table '{table_name}': "
                "CompFilterColName is empty in the table-mapping XML and no "
                "non-numeric columns exist to use as fallback identifiers."
            )}
        comp_cols = [c.upper() for c in auto_id]
        logger.warning(
            "[row_match] CompFilterColName is empty for table '%s'. "
            "Auto-detected identifier columns from non-numeric columns: %s. "
            "Set CompFilterColName in the table-mapping XML for reliable matching.",
            table_name, comp_cols,
        )
        # Recompute all_display_cols now that comp_cols is known
        all_display_cols = [
            k for k in all_rows[0].keys()
            if k.upper() != fc and k.upper() not in set(comp_cols)
        ]

    logger.info("[row_match] Identifier columns: %s", comp_cols)

    # ── Build display-label columns (best-effort human-readable label) ────
    label_cols = _pick_label_columns(list(all_rows[0].keys()), comp_cols, rows=all_rows)
    logger.info("[variance] Display-label columns: %s", label_cols)

    # Exclude hint-based label columns from display_columns — they are already
    # surfaced via display_label on each row, showing them again duplicates data.
    non_comp_label_set = {c.upper() for c in label_cols if c.upper() not in set(comp_cols)}
    if non_comp_label_set:
        all_display_cols = [k for k in all_display_cols if k.upper() not in non_comp_label_set]
        selected_columns = [c for c in selected_columns if c.upper() not in non_comp_label_set]
        logger.info(
            "[variance] Excluded hint-based label columns from display (already in display_label): %s",
            non_comp_label_set,
        )

    # ── Build previous-period lookup tables keyed by business identifier ──
    prev_row_sets: Dict[str, Any] = {}
    for i, pd in enumerate(prev_dates):
        period_rows = [r for r in all_rows if dates_match(r.get(fc), pd)]
        lookup: Dict[str, Any] = {}
        duplicate_ids: set = set()
        for row in period_rows:
            ident = build_identifier(row, comp_cols)
            if ident in lookup:
                duplicate_ids.add(ident)
            lookup[ident] = row
        if duplicate_ids:
            logger.error(
                "[row_match] Duplicate previous-row identifiers on %s: %s | "
                "Identifier columns=%s. "
                "Add more columns to CompFilterColName in the table-mapping XML.",
                pd.strftime('%d-%b-%Y'), sorted(duplicate_ids), comp_cols,
            )
        logger.info(
            "[row_match] Previous period %d (%s): %d rows, %d unique identifiers",
            i + 1, pd.strftime("%d-%b-%Y"), len(period_rows), len(lookup),
        )
        prev_row_sets[f"previous_{i + 1}"] = {"date": pd, "lookup": lookup}

    # ── Pre-build per-date lookups for sequential mode ───────────────────
    date_lookups: List[Dict[str, Any]] = []
    if comparison_mode == "sequential":
        chronological_pre = list(reversed(prev_dates)) + [rdate]
        for d in chronological_pre:
            period_rows = [r for r in all_rows if dates_match(r.get(fc), d)]
            lookup: Dict[str, Any] = {}
            for row in period_rows:
                ident = build_identifier(row, comp_cols)
                lookup[ident] = row
            date_lookups.append(lookup)

    result_rows = []
    seen_current_ids: set = set()

    for curr_row in current_rows:
        identifier = build_identifier(curr_row, comp_cols)

        if identifier in seen_current_ids:
            logger.error(
                "[row_match] Duplicate current-row identifier: '%s' | "
                "Identifier columns=%s. "
                "Add more columns to CompFilterColName so the key is unique.",
                identifier, comp_cols,
            )
        seen_current_ids.add(identifier)

        row_result: Dict[str, Any] = {
            "identifier":    identifier,
            "display_label": build_identifier(curr_row, label_cols) or identifier,
            "current":       curr_row,
            "previous":      {},
        }

        if comparison_mode == "sequential":
            # ── Sequential mode: chain each consecutive date pair ────────
            chronological = list(reversed(prev_dates)) + [rdate]
            seq_links = list(zip(chronological, chronological[1:]))

            for link_idx, (date_a, date_b) in enumerate(seq_links):
                from_row = date_lookups[link_idx].get(identifier)
                # For the last link, to_row IS curr_row (already available)
                if link_idx == len(seq_links) - 1:
                    to_row = curr_row
                else:
                    to_row = date_lookups[link_idx + 1].get(identifier)

                link_metrics: Dict[str, Any] = {}
                if from_row is not None and to_row is not None:
                    for col in selected_columns:
                        col_up = col.upper()
                        from_v = from_row.get(col_up)
                        to_v   = to_row.get(col_up)
                        logger.info(
                            "[seq_link] Identifier=%s | Col=%s | from=%s | to=%s",
                            identifier, col, from_v, to_v,
                        )
                        link_metrics[col] = {
                            "value":            from_v,
                            "change":           get_difference(from_v, to_v),
                            "pct_change":       get_pct_change(from_v, to_v),
                            "variance_summary": get_variance_summary(from_v, to_v),
                        }
                elif from_row is None:
                    logger.warning(
                        "[seq_link] No row for date_a=%s | Identifier=%s",
                        date_a.strftime("%d-%b-%Y"), identifier,
                    )

                link_key = f"link_{link_idx + 1}"
                row_result[link_key] = {
                    "from_date": date_a.strftime("%d-%b-%Y").upper(),
                    "to_date":   date_b.strftime("%d-%b-%Y").upper(),
                    "metrics":   link_metrics,
                }

            # Expose last link's metrics as previous_1 for backward-compat
            last_link_key = f"link_{len(seq_links)}"
            if last_link_key in row_result:
                row_result["previous"]["previous_1"] = row_result[last_link_key]["metrics"]

        else:
            # ── vs_current mode: existing behavior ──────────────────────
            for period_key, pdata in prev_row_sets.items():
                matched = pdata["lookup"].get(identifier)
                if not matched:
                    logger.warning(
                        "[row_match] No previous row found | Identifier=%s | period=%s",
                        identifier, period_key,
                    )
                    continue

                metrics: Dict[str, Any] = {}
                for col in selected_columns:
                    col_up = col.upper()
                    prev_v = matched.get(col_up)
                    curr_v = curr_row.get(col_up)
                    logger.info(
                        "[row_match] Identifier=%s | Col=%s | Current=%s | Previous=%s",
                        identifier, col, curr_v, prev_v,
                    )
                    metrics[col] = {
                        "value":            prev_v,
                        "change":           get_difference(prev_v, curr_v),
                        "pct_change":       get_pct_change(prev_v, curr_v),
                        "variance_summary": get_variance_summary(prev_v, curr_v),
                    }
                row_result["previous"][period_key] = metrics

        result_rows.append(row_result)

    chronological = list(reversed(prev_dates)) + [rdate]
    return {
        "table_name":         table_name,
        "reporting_date":     reporting_date,
        "comparison_periods": [pd.strftime("%d-%b-%Y").upper() for pd in prev_dates],
        "columns":            selected_columns,
        "display_columns":    all_display_cols,
        "rows":               result_rows,
        "comparison_mode":    comparison_mode,
        "chain_dates":        (
            [d.strftime("%d-%b-%Y").upper() for d in chronological]
            if comparison_mode == "sequential"
            else []
        ),
    }
