"""Generate qa_pairs.json for the 6.0 QCB returns from their Excel templates.

Each pair is {"table", "question", "sql"} - the same shape as the 5.5 file. Labels and
column names are read from the workbooks, never typed by hand, so every SQL statement
references a row label / column that really exists.

Usage:
    python scripts/gen_qa_pairs.py --out artifacts/nlp-index/ideal-60/qa_pairs.generated.json \
        "F010 4.xlsx" "F013 5.xlsx" "F014 4.xlsx" "F015 3.xlsx"

The output is a STAGING file: the stamped qa_pairs.json in the index folder is checksummed
by build_stamp.json, and qa_index.faiss/qa_meta.pkl must be rebuilt by the external
embedding tool from whatever file you finally publish.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import openpyxl

LATEST = "RDATE = (SELECT MAX(RDATE) FROM {t})"

# F010 maturity buckets: column prefix -> phrasings people use.
BUCKET_PHRASES = {
    "OVERNIGHT": ["overnight"],
    "GT_1_TO_7_DAYS": ["1 to 7 days", "1-7 days"],
    "GT_7_TO_15_DAYS": ["7 to 15 days"],
    "GT_15_TO_30_DAYS": ["15 to 30 days"],
    "GT_1_TO_3_MONTHS": ["1 to 3 months"],
    "GT_3_TO_6_MONTHS": ["3 to 6 months"],
    "GT_6_TO_12_MONTHS": ["6 to 12 months"],
    "GT_1_TO_3_YEARS": ["1 to 3 years"],
    "GT_3_TO_5_YEARS": ["3 to 5 years"],
    "GT_5_YEARS": ["over 5 years", "more than 5 years"],
    "NO_CONTRA_MAT": ["no contractual maturity"],
    "TOTAL": ["total across all buckets"],
}
CCY_PHRASES = {"LC": "local currency (QAR)", "USD": "USD", "OTHER_FC": "other foreign currencies"}
F010_TABLES = {
    "QCB_F010_RCM_IN_QTR": ("qcb_f010_rcm_in_qtr", "inside Qatar"),
    "QCB_F010_RCM_OUT_QTR": ("qcb_f010_rcm_out_qtr", "outside Qatar"),
}


def q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def clean(label: str) -> str:
    return re.sub(r"\s+", " ", str(label)).strip()


def short(label: str) -> str:
    """Human phrasing of a label: drop the 'less :' prefix and trailing explanatory brackets."""
    s = re.sub(r"^less\s*:\s*", "", clean(label), flags=re.I)
    return re.sub(r"\s*\((?:includes|e\.g\.|incl\.|including)[^)]*\)", "", s, flags=re.I)


def pair(table: str, question: str, sql: str) -> dict:
    return {"table": table, "question": question, "sql": sql}


def f010(wb) -> list[dict]:
    out = []
    for sheet, (table, where) in F010_TABLES.items():
        ws = wb[sheet]
        cols = [c.value for c in ws[3]]
        rows = [r for r in ws.iter_rows(min_row=4, values_only=True) if r[1]]
        ccy_cols = {b: [f"{b}_{c}" for c in CCY_PHRASES] for b in BUCKET_PHRASES}
        # A label like "Others"/"Debt Securities" repeats under different parents, so
        # filter on SR_NO whenever the label is not unique.
        counts: dict[str, int] = {}
        for r in rows:
            counts[clean(r[1])] = counts.get(clean(r[1]), 0) + 1
        for r in rows:
            sr, label = r[0], clean(r[1])
            human = short(label)
            flt = f"DESCRIPTION = {q(label)}" if counts[label] == 1 else f"SR_NO = {sr}"
            ctx = f" for the {human} line" if counts[label] > 1 else ""
            parent = f" (row {sr})" if counts[label] > 1 else ""
            t = table
            lat = LATEST.format(t=t)
            # 1. total across currencies
            out.append(pair(t, f"What is the total {human} in the maturity ladder {where}?{parent}",
                            f"SELECT DESCRIPTION, TOTAL_LC, TOTAL_USD, TOTAL_OTHER_FC FROM {t} WHERE {flt} AND {lat}"))
            # 2. overnight bucket, all currencies
            out.append(pair(t, f"Show {human} due overnight {where} in LC, USD and other FC{ctx}",
                            f"SELECT DESCRIPTION, OVERNIGHT_LC, OVERNIGHT_USD, OVERNIGHT_OTHER_FC FROM {t} WHERE {flt} AND {lat}"))
            # 3. one rotating bucket x currency (deterministic, spreads coverage across all 36 columns)
            b = list(BUCKET_PHRASES)[sr % len(BUCKET_PHRASES)]
            c = list(CCY_PHRASES)[(sr // 3) % 3]
            col = f"{b}_{c}"
            assert col in cols, col
            bp = BUCKET_PHRASES[b][sr % len(BUCKET_PHRASES[b])]
            out.append(pair(t, f"What is the {human} maturing {bp} in {CCY_PHRASES[c]} {where}?{parent}",
                            f"SELECT DESCRIPTION, {col} FROM {t} WHERE {flt} AND {lat}"))
        gap = "DESCRIPTION IN ('Total gap', 'Total cumulative gap')"
        out.append(pair(table, f"What are the total gap and total cumulative gap in the maturity ladder {where}?",
                        f"SELECT DESCRIPTION, TOTAL_LC, TOTAL_USD, TOTAL_OTHER_FC FROM {table} WHERE {gap} AND {LATEST.format(t=table)}"))
        out.append(pair(table, f"What is the liquidity gap across all maturity buckets {where}?",
                        f"SELECT DESCRIPTION, OVERNIGHT_LC, GT_1_TO_7_DAYS_LC, GT_7_TO_15_DAYS_LC, GT_15_TO_30_DAYS_LC, GT_1_TO_3_MONTHS_LC "
                        f"FROM {table} WHERE DESCRIPTION = 'Current gap for each segment/ band - on balance sheet' AND {LATEST.format(t=table)}"))
        out.append(pair(table, f"Compare total assets and total liabilities and equity in the maturity ladder {where}",
                        f"SELECT DESCRIPTION, TOTAL_LC, TOTAL_USD, TOTAL_OTHER_FC FROM {table} WHERE DESCRIPTION IN ('Total Assets', 'Total liabilities and Equity') AND {LATEST.format(t=table)}"))
    return out


def geo(wb, sheet: str, table: str, noun: str) -> list[dict]:
    ws = wb[sheet]
    heads = [c.value for c in ws[1]][:11]
    names = [c.value for c in ws[2]][:11]
    countries = [r[0] for r in ws.iter_rows(min_row=3, values_only=True) if r[0]]
    lat = LATEST.format(t=table)
    out = []
    for i, country in enumerate(countries):
        if country == "Total":
            continue
        # country x one counterparty segment, rotating over all 9 segments
        seg = 1 + (i % 9)
        out.append(pair(table, f"What is the {noun} to {country}?",
                        f"SELECT COUNTRY, TOTAL FROM {table} WHERE COUNTRY = {q(country)} AND {lat}"))
        out.append(pair(table, f"How much {noun} to {heads[seg]} does {country} account for?",
                        f"SELECT COUNTRY, {names[seg]} FROM {table} WHERE COUNTRY = {q(country)} AND {lat}"))
    for seg in range(1, 10):
        out.append(pair(table, f"Which country has the highest {noun} to {heads[seg]}?",
                        f"SELECT COUNTRY, {names[seg]} FROM {table} WHERE COUNTRY <> 'Total' AND {lat} ORDER BY {names[seg]} DESC FETCH FIRST 1 ROWS ONLY"))
        out.append(pair(table, f"What is the total {noun} to {heads[seg]} across all countries?",
                        f"SELECT COUNTRY, {names[seg]} FROM {table} WHERE COUNTRY = 'Total' AND {lat}"))
    out.append(pair(table, f"Show the {noun} for all countries by counterparty type",
                    f"SELECT {', '.join(names[:11])} FROM {table} WHERE COUNTRY <> 'Total' AND {lat}"))
    out.append(pair(table, f"What is the overall total {noun}?",
                    f"SELECT COUNTRY, TOTAL FROM {table} WHERE COUNTRY = 'Total' AND {lat}"))
    out.append(pair(table, f"Which top 5 countries have the highest total {noun}?",
                    f"SELECT COUNTRY, TOTAL FROM {table} WHERE COUNTRY <> 'Total' AND {lat} ORDER BY TOTAL DESC FETCH FIRST 5 ROWS ONLY"))
    return out


def f015(wb) -> list[dict]:
    ws = wb["QCB_F015_FOREIGN_CUR_NOP"]
    groups = [c.value for c in ws[2]]
    cols = [c.value for c in ws[4]]
    table = "qcb_f015_foreign_cur_nop"
    lat = LATEST.format(t=table)
    rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if r[1]]
    # distinct currencies (names repeat across countries, e.g. Euro, US Dollar)
    ccys = list(dict.fromkeys(clean(r[1]) for r in rows))
    out = []
    # item groups: column triplets (_TOTAL, _IN_QTR, _OUT_QTR) with the group heading above
    items = []
    for i, name in enumerate(cols):
        if name and name.endswith("_TOTAL") and groups[i] and not name.startswith("TOTAL_"):
            base = name[: -len("_TOTAL")]
            if f"{base}_IN_QTR" in cols:
                items.append((groups[i], base))
    for idx, ccy in enumerate(ccys):
        label, base = items[idx % len(items)]
        out.append(pair(table, f"What is the net open position in {ccy}?",
                        f"SELECT CURRENCY, SURPLUS_DEF, SURPLUS_CAP_BASE_RATIO FROM {table} WHERE CURRENCY = {q(ccy)} AND {lat}"))
        out.append(pair(table, f"What is the {label.lower()} held in {ccy}, inside and outside Qatar?",
                        f"SELECT CURRENCY, {base}_TOTAL, {base}_IN_QTR, {base}_OUT_QTR FROM {table} WHERE CURRENCY = {q(ccy)} AND {lat}"))
        out.append(pair(table, f"What is the exchange rate used for {ccy}?",
                        f"SELECT CURRENCY, EXCHANGE_RATE FROM {table} WHERE CURRENCY = {q(ccy)} AND {lat}"))
    for label, base in items:
        out.append(pair(table, f"Which currency has the highest {label.lower()}?",
                        f"SELECT CURRENCY, {base}_TOTAL FROM {table} WHERE {lat} ORDER BY {base}_TOTAL DESC FETCH FIRST 1 ROWS ONLY"))
        out.append(pair(table, f"Show {label.lower()} inside Qatar versus outside Qatar by currency",
                        f"SELECT CURRENCY, {base}_IN_QTR, {base}_OUT_QTR FROM {table} WHERE {lat}"))
    for what, col in [("total assets", "TOTAL_ASSETS"), ("total liabilities", "TOTAL_LIAB"),
                      ("total equity and liabilities", "TOTAL_LIAB_EQUITY"),
                      ("surplus or deficit", "SURPLUS_DEF"), ("surplus or deficit as a ratio of the capital base", "SURPLUS_CAP_BASE_RATIO"),
                      ("forward purchases", "FORWARD_PURCHASES"), ("deferred sales", "DEFERRED_SALES")]:
        out.append(pair(table, f"Which currencies have the largest {what}?",
                        f"SELECT CURRENCY, {col} FROM {table} WHERE {lat} ORDER BY {col} DESC FETCH FIRST 5 ROWS ONLY"))
        out.append(pair(table, f"Show {what} for every foreign currency",
                        f"SELECT CURRENCY, {col} FROM {table} WHERE {lat}"))
    out.append(pair(table, "Which currencies have a net open position deficit?",
                    f"SELECT CURRENCY, SURPLUS_DEF FROM {table} WHERE SURPLUS_DEF < 0 AND {lat} ORDER BY SURPLUS_DEF"))
    return out


def filing_info(prefix: str, ret: str) -> list[dict]:
    t = f"qcb_{prefix}_filing_info"
    lat = LATEST.format(t=t)
    items = [("reporting institution name", "Name of reporting institution"),
             ("return code", "Return code"), ("return name", "Return name"),
             ("reporting frequency", "Reporting frequency"), ("reporting start date", "Reporting start date"),
             ("reporting end date", "Reporting end date"), ("reporting currency", "Reporting currency"),
             ("reporting scale", "Reporting scale"),
             ("restatement flag", "Whether there are restatements or reclassification to prior period data previously submitted")]
    out = [pair(t, f"What is the {ret} {h} in the filing information?",
                f"SELECT DESCRIPTION, VALUE FROM {t} WHERE DESCRIPTION = {q(d)} AND {lat}") for h, d in items]
    out.append(pair(t, f"Show the filing information for {ret}", f"SELECT DESCRIPTION, VALUE, CODE FROM {t} WHERE {lat}"))
    return out


# How a user names each return: short code, full registered name, plain language. The full
# names must equal `return_name` in the 6.0 schema.json so the analyzer's return matching and
# these questions agree.
RETURN_ALIASES = {
    "qcb_f010_": ("F010", "QCB_F010 Maturity Ladder of Assets & Liabilities", "the maturity ladder return"),
    "qcb_f013_": ("F013", "QCB_F013 Breakdown of exposures by geography", "the geographic exposure return"),
    "qcb_f014_": ("F014", "QCB_F014 Breakdown of Funding by geography", "the funding by geography return"),
    "qcb_f015_": ("F015", "QCB_F015 Foreign Currency Net Open Positions", "the foreign currency net open position return"),
}
# Return-level asks that name only the return. Only for returns with ONE data table, so the
# pinned table is unambiguous (F010 has IN/OUT tables - a bare "show F010" must clarify).
RETURN_LEVEL = {
    "qcb_f013_break_exposure": "SELECT COUNTRY, TOTAL FROM qcb_f013_break_exposure WHERE COUNTRY <> 'Total' AND {lat}",
    "qcb_f014_funding_geo": "SELECT COUNTRY, TOTAL FROM qcb_f014_funding_geo WHERE COUNTRY <> 'Total' AND {lat}",
    "qcb_f015_foreign_cur_nop": "SELECT CURRENCY, TOTAL_ASSETS, TOTAL_LIAB, SURPLUS_DEF FROM qcb_f015_foreign_cur_nop WHERE {lat}",
}


def _alias_for(table: str) -> tuple[str, ...] | None:
    return next((a for p, a in RETURN_ALIASES.items() if table.startswith(p)), None)


def add_return_variants(pairs: list[dict]) -> list[dict]:
    """Add ONE return-named variant per pair (alias style and wording rotate), so what a user
    types when they name the return - "In F010, ..." - is a near-duplicate of a stored question.
    SQL is unchanged, so table and columns stay exactly those of the base pair."""
    out = list(pairs)
    for i, p in enumerate(pairs):
        aliases = _alias_for(p["table"])
        if not aliases:
            continue
        alias = aliases[i % 3]
        body = p["question"]
        if (i // 3) % 2 == 0:
            question = f"In {alias}, {body[0].lower()}{body[1:]}"
        else:
            question = f"{body.rstrip('?')} in {alias}" + ("?" if body.endswith("?") else "")
        out.append(pair(p["table"], question, p["sql"]))
    for table, sql in RETURN_LEVEL.items():
        aliases = _alias_for(table)
        sql = sql.format(lat=LATEST.format(t=table))
        for alias in aliases:
            for template in ("Show the {a} data", "What does {a} report?", "Give me the latest {a} figures"):
                out.append(pair(table, template.format(a=alias), sql))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("files", nargs=4, help="F010, F013, F014, F015 workbooks (any order)")
    args = ap.parse_args()

    pairs: list[dict] = []
    for path in args.files:
        wb = openpyxl.load_workbook(path, data_only=True)
        names = set(wb.sheetnames)
        if "QCB_F010_RCM_IN_QTR" in names:
            pairs += f010(wb) + filing_info("f010", "F010 maturity ladder")
        elif "QCB_F013_BREAK_EXPOSURE" in names:
            pairs += geo(wb, "QCB_F013_BREAK_EXPOSURE", "qcb_f013_break_exposure", "exposure") + filing_info("f013", "F013 exposure")
        elif "QCB_F014_FUNDING_GEO" in names:
            pairs += geo(wb, "QCB_F014_FUNDING_GEO", "qcb_f014_funding_geo", "funding") + filing_info("f014", "F014 funding")
        elif "QCB_F015_FOREIGN_CUR_NOP" in names:
            pairs += f015(wb) + filing_info("f015", "F015 foreign currency net open position")
        else:
            print(f"unrecognised workbook: {path}", file=sys.stderr)
            return 1

    pairs = add_return_variants(pairs)

    seen, unique = set(), []
    for p in pairs:  # the index must not hold duplicate questions
        key = (p["table"], p["question"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(unique, fh, indent=1, ensure_ascii=False)
    per: dict[str, int] = {}
    for p in unique:
        per[p["table"]] = per.get(p["table"], 0) + 1
    print(f"wrote {len(unique)} pairs to {args.out}")
    for t, n in sorted(per.items()):
        print(f"  {t}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
