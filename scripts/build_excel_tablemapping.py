"""build_excel_tablemapping.py — produce excel_tablemapping.json for iDEAL 6.0.

WHY THIS EXISTS
    The 5.5 embedding pipeline was seeded from an Oracle export
    (data/.json-formatted) pairing every physical column with its
    human-readable label. No such export exists for the 6.0 / QCB returns, so
    the NLP layer has nothing useful to embed: a retriever that only ever sees
    PVT_SEC_LARGE_CORP cannot match a user asking about "large corporates".

    The labels do exist — in the QCB submission templates. Each data sheet
    carries human header rows directly above the physical column names, so this
    script reads that pairing out of the workbooks and joins it to the live
    Oracle catalogue.

SHAPE
    Output matches data/.json-formatted exactly (results[0].columns / items),
    so it feeds the same downstream tooling with no second format to support.

HEADER LAYOUT (detected, not assumed)
    The physical-column row is the LAST header row; everything above it is
    label hierarchy, which may be one to three rows deep and heavily merged:

        F013 / F014   r1 labels        r2 DB names
        F010          r1-r2 labels     r3 DB names
        F015          r1-r3 labels     r4 DB names

    Rather than hard-code a depth per file, the DB-name row is found by scoring
    each candidate row against the table's real column list from Oracle. That
    keeps this correct when a new template nests its headers differently.

USAGE
    python scripts/build_excel_tablemapping.py <file.xlsx> [more.xlsx ...] \
        [--out backend/data6.0/excel_tablemapping.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402

from backend.data.db import execute_query  # noqa: E402

# Columns Oracle carries that never appear in a template, described with the
# meaning the variance engine already assigns them (calculate_variance filters
# on RDATE and keys rows on CODE).
SYSTEM_COLUMN_DESCRIPTIONS = {
    "RDATE": "Reporting date",
    "CODE": "Row code",
    "TRANSATION_ID": "Transaction ID",
    "TRANSACTION_ID": "Transaction ID",
    "REMARK": "Remarks",
    "REMARKS": "Remarks",
}

# Key-value tables (FILING_INFO, BANK_CAP_BASE) hold one fact per ROW, so their
# columns are structural rather than semantic and no template header describes
# them. Naming them explicitly beats echoing the column name back as its own
# description, which would embed no information at all.
KEY_VALUE_COLUMN_DESCRIPTIONS = {
    "DESCRIPTION": "Filing information item name",
    "VALUE": "Filing information item value",
    "CODE": "IRIS code identifying the filing information item",
    "RDATE": "Reporting date",
}

KEY_VALUE_TABLE_SUFFIXES = ("FILING_INFO", "BANK_CAP_BASE")

# Header cells that were corrupted during template editing and now carry a
# spreadsheet fragment instead of a label. Real example, F010 4.xlsx:
#   QCB_F010_RCM_IN_QTR!B1 = "Particul+C3+B+B1:K2"
# while the identical sibling sheet QCB_F010_RCM_OUT_QTR!B1 = "Particulars".
# Embedding the corrupted text would make that column unsearchable, so such
# labels are detected and recovered from a sibling sheet where possible.
_ARTIFACT_RE = re.compile(r"[A-Z]+\d+:[A-Z]+\d+|\+[A-Z]\d|\+[A-Z]+\+")


def looks_corrupted(text: str) -> bool:
    return bool(_ARTIFACT_RE.search(text or ""))


# ── Oracle catalogue ──────────────────────────────────────────────────────────

def fetch_db_columns(schema: str, table_regex: str) -> Dict[str, List[str]]:
    """{TABLE_NAME: [COLUMN, ...]} in physical column order.

    _DP and _HIST are excluded. _DP is the same shape — the variance engine
    appends that suffix itself at query time (service._resolve_physical_table_name)
    — and _HIST is an archive. Emitting all three would triple the index with
    near-identical text and let the retriever answer with an archive table.
    """
    sql = f"""
        SELECT table_name, column_name
        FROM all_tab_columns
        WHERE owner = '{schema}'
          AND REGEXP_LIKE(UPPER(table_name), '{table_regex}')
          AND table_name NOT LIKE '%\\_DP' ESCAPE '\\'
          AND table_name NOT LIKE '%\\_HIST' ESCAPE '\\'
        ORDER BY table_name, column_id
    """
    _cols, rows, err = execute_query(sql)
    if err:
        raise RuntimeError(f"Oracle catalogue query failed: {err}")

    out: Dict[str, List[str]] = defaultdict(list)
    for table_name, column_name in rows or []:
        out[str(table_name).strip().upper()].append(str(column_name).strip().upper())
    return dict(out)


# ── Worksheet reading ─────────────────────────────────────────────────────────

def expand_merged(ws) -> List[List[Any]]:
    """Grid with every merged range filled across all the cells it covers.

    openpyxl reports a merged range's value only in its top-left cell and None
    elsewhere. A header like "Assets" spanning twelve columns would otherwise
    label only the first of them, silently dropping the group name from the
    other eleven descriptions.
    """
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    for rng in ws.merged_cells.ranges:
        r0, c0, r1, c1 = rng.min_row, rng.min_col, rng.max_row, rng.max_col
        if r0 - 1 >= len(grid) or c0 - 1 >= len(grid[r0 - 1]):
            continue
        value = grid[r0 - 1][c0 - 1]
        if value is None:
            continue
        for r in range(r0 - 1, min(r1, len(grid))):
            for c in range(c0 - 1, min(c1, len(grid[r]))):
                grid[r][c] = value
    return grid


def find_db_name_row(
    grid: List[List[Any]], db_columns: List[str], scan_depth: int = 8
) -> Optional[int]:
    """0-based index of the row holding physical column names, or None.

    Scored against the real Oracle column list rather than pattern-matched:
    template labels are sometimes themselves ALL-CAPS ("DESCRIPTION", "VALUE")
    and a pattern match would mistake them for the physical row.
    """
    db_set = set(db_columns)
    best_row, best_hits = None, 0
    for r in range(min(scan_depth, len(grid))):
        hits = sum(
            1 for v in grid[r]
            if v is not None and str(v).strip().upper() in db_set
        )
        # >= so ties resolve to the LOWEST row: a label can coincidentally match
        # a column name, but the physical row is always the last header row.
        if hits > 0 and hits >= best_hits:
            best_row, best_hits = r, hits
    return best_row


def compose_label(grid: List[List[Any]], label_rows: List[int], col: int) -> str:
    """Join the header hierarchy above a column into one description.

    F015's CASH_EQ_IN_QTR sits under Assets > Cash and Cash Equivalents >
    Inside Qatar. Flattening to "Inside Qatar" alone would be indistinguishable
    from the eleven other "Inside Qatar" columns in the same sheet.
    """
    parts: List[str] = []
    for r in label_rows:
        if r >= len(grid) or col >= len(grid[r]):
            continue
        v = grid[r][col]
        if v is None:
            continue
        text = " ".join(str(v).split())
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    return " - ".join(parts)


def read_filing_info(ws) -> Dict[str, str]:
    """{'Return code': 'F014', 'Return name': ..., 'Reporting scale': ...}."""
    meta: Dict[str, str] = {}
    for row in ws.iter_rows(values_only=True):
        cells = [c for c in row if c is not None and str(c).strip()]
        if len(cells) >= 2:
            meta[str(cells[0]).strip()] = str(cells[1]).strip()
    return meta


def is_key_value_table(table: str) -> bool:
    return table.upper().endswith(KEY_VALUE_TABLE_SUFFIXES)


def describe_fallback(col: str, key_value: bool) -> Optional[str]:
    if key_value and col in KEY_VALUE_COLUMN_DESCRIPTIONS:
        return KEY_VALUE_COLUMN_DESCRIPTIONS[col]
    return SYSTEM_COLUMN_DESCRIPTIONS.get(col)


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_workbook(
    path: str,
    db_columns: Dict[str, List[str]],
    return_name_overrides: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(items, warnings) for one template workbook."""
    wb = openpyxl.load_workbook(path, data_only=True)
    warnings: List[str] = []
    items: List[Dict[str, Any]] = []
    base = os.path.basename(path)

    filing_sheet = next(
        (ws for ws in wb.worksheets if ws.title.upper().endswith("FILING_INFO")),
        wb.worksheets[0],
    )
    meta = read_filing_info(filing_sheet)
    return_code = meta.get("Return code", "").strip()
    scale = meta.get("Reporting scale", "").strip()
    return_name = return_name_overrides.get(
        return_code, meta.get("Return name", return_code)
    )

    for sheet_no, ws in enumerate(wb.worksheets, start=1):
        table = ws.title.strip().upper()
        cols = db_columns.get(table)
        if not cols:
            warnings.append(
                f"{base} :: sheet {ws.title!r} has no matching Oracle table — skipped"
            )
            continue

        key_value = is_key_value_table(table)
        described: Dict[str, str] = {}

        if not key_value:
            grid = expand_merged(ws)
            db_row = find_db_name_row(grid, cols)
            if db_row is None:
                warnings.append(
                    f"{base} :: sheet {ws.title!r} — could not locate the "
                    f"physical-column header row; using column names as descriptions"
                )
            else:
                label_rows = list(range(db_row))
                for c, v in enumerate(grid[db_row]):
                    if v is None:
                        continue
                    name = str(v).strip().upper()
                    if name not in cols:
                        continue
                    label = compose_label(grid, label_rows, c)
                    if label:
                        described[name] = label

        for col in cols:
            description = described.get(col) or describe_fallback(col, key_value)
            if description is None:
                # Echo the column name. Carries no extra signal, but keeps the
                # row present so a later pass can see what still needs a label.
                description = col
                warnings.append(
                    f"{table}.{col} — no template label found, using column name"
                )
            items.append({
                "table_name": table,
                "column_name": col,
                "column_Description": description,
                "template_sheet_no": sheet_no,
                "return_name": return_name,
                "isactive": 1,
                "scale": scale,
            })

    # Tables that exist in Oracle for this return but have no sheet of their own
    # (F015 declares QCB_F015_BANK_CAP_BASE) still need their columns indexed,
    # or the retriever cannot reach them at all.
    sheet_tables = {ws.title.strip().upper() for ws in wb.worksheets}
    if return_code:
        prefix = f"QCB_{return_code.upper()}_"
        for table, cols in sorted(db_columns.items()):
            if not table.startswith(prefix) or table in sheet_tables:
                continue
            warnings.append(
                f"{table} — in Oracle for {return_code} but has no template sheet; "
                f"indexed with structural descriptions only"
            )
            key_value = is_key_value_table(table)
            for col in cols:
                items.append({
                    "table_name": table,
                    "column_name": col,
                    "column_Description": describe_fallback(col, key_value) or col,
                    "template_sheet_no": 0,
                    "return_name": return_name,
                    "isactive": 1,
                    "scale": scale,
                })

    _recover_corrupted_labels(items, warnings, base)
    return items, warnings


def _recover_corrupted_labels(
    items: List[Dict[str, Any]], warnings: List[str], source: str
) -> None:
    """Replace corrupted header text with a clean label for the same column.

    Only ever copies from another table in the SAME workbook that (a) describes
    the same column name and (b) is the same KIND of table, so the replacement
    is evidence from the template rather than an invention.

    The kind check matters: DESCRIPTION means "row label" in a data table like
    RCM_IN_QTR but "filing information item name" in a key-value table like
    FILING_INFO. Ignoring the distinction recovers a confidently wrong label.
    Among equally valid candidates the one sharing the longest name prefix wins,
    which picks the paired sheet (RCM_OUT_QTR for RCM_IN_QTR) over a distant one.

    Anything unrecoverable is left as-is and warned about, so a bad label is
    never silently passed off as good.
    """
    def prefix_len(a: str, b: str) -> int:
        n = 0
        for ca, cb in zip(a, b):
            if ca != cb:
                break
            n += 1
        return n

    clean = [
        it for it in items if not looks_corrupted(it["column_Description"])
    ]

    for it in items:
        desc = it["column_Description"]
        if not looks_corrupted(desc):
            continue

        want_kv = is_key_value_table(it["table_name"])
        candidates = [
            c for c in clean
            if c["column_name"] == it["column_name"]
            and c["table_name"] != it["table_name"]
            and is_key_value_table(c["table_name"]) == want_kv
        ]
        if candidates:
            best = max(
                candidates,
                key=lambda c: prefix_len(c["table_name"], it["table_name"]),
            )
            it["column_Description"] = best["column_Description"]
            warnings.append(
                f"{source} :: {it['table_name']}.{it['column_name']} — header cell "
                f"is corrupted ({desc!r}); recovered "
                f"{best['column_Description']!r} from {best['table_name']}. "
                f"Fix the template."
            )
        else:
            warnings.append(
                f"{source} :: {it['table_name']}.{it['column_name']} — header cell "
                f"is corrupted ({desc!r}) and no comparable sheet describes this "
                f"column; left as-is. Fix the template."
            )


# ── Output ────────────────────────────────────────────────────────────────────

OUTPUT_COLUMNS = [
    {"name": "TABLE_NAME", "type": "VARCHAR2"},
    {"name": "column_name", "type": "VARCHAR2"},
    {"name": "column_Description", "type": "VARCHAR2"},
    {"name": "TEMPLATE_SHEET_NO", "type": "NUMBER"},
    {"name": "RETURN_NAME", "type": "VARCHAR2"},
    {"name": "ISACTIVE", "type": "NUMBER"},
    {"name": "SCALE", "type": "VARCHAR2"},
]

# Names exactly as they appear in Repo6's Return.xml. The retriever filters
# candidates by the caller's allowed return ids, which are resolved through that
# file, so a return_name invented here would never match anything.
RETURN_NAME_OVERRIDES = {
    "F010": "QCB_F010_Maturity Ladder of Assets & Liabilities",
    "F013": "QCB_F013_Breakdown of exposures by geography",
    "F014": "QCB_F014_Breakdown of Funding by geography",
    "F015": "QCB_F015_Foreign Currency Net Open Positions",
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build excel_tablemapping.json from QCB template workbooks."
    )
    ap.add_argument("workbooks", nargs="+", help="QCB template .xlsx files")
    ap.add_argument(
        "--out", default="backend/data6.0/excel_tablemapping.json",
        help="output path (default: backend/data6.0/excel_tablemapping.json)",
    )
    ap.add_argument("--schema", default="IDEALCRILC", help="Oracle owner")
    ap.add_argument(
        "--table-regex", default="^QCB_F(010|013|014|015)_",
        help="which Oracle tables to consider",
    )
    args = ap.parse_args()

    print(f"Reading Oracle catalogue (owner={args.schema}) ...")
    db_columns = fetch_db_columns(args.schema, args.table_regex)
    print(f"  {len(db_columns)} table(s), "
          f"{sum(len(v) for v in db_columns.values())} column(s)\n")

    all_items: List[Dict[str, Any]] = []
    all_warnings: List[str] = []
    seen: set = set()

    for path in args.workbooks:
        print(f"Reading {os.path.basename(path)} ...")
        items, warnings = extract_workbook(path, db_columns, RETURN_NAME_OVERRIDES)
        added = 0
        for it in items:
            key = (it["table_name"], it["column_name"])
            if key in seen:
                continue
            seen.add(key)
            all_items.append(it)
            added += 1
        tables = sorted({it["table_name"] for it in items})
        print(f"  {added} column(s) across {len(tables)} table(s)")
        for t in tables:
            n = sum(1 for it in items if it["table_name"] == t)
            print(f"     {t:<38} {n:>3}")
        all_warnings.extend(warnings)
        print()

    payload = {"results": [{"columns": OUTPUT_COLUMNS, "items": all_items}]}

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    print("=" * 72)
    print(f"Wrote {args.out}")
    print(f"  tables : {len({it['table_name'] for it in all_items})}")
    print(f"  columns: {len(all_items)}")
    print(f"  returns: {len({it['return_name'] for it in all_items})}")

    if all_warnings:
        print(f"\n{len(all_warnings)} warning(s):")
        for w in all_warnings:
            print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
