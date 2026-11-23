"""End-to-end accuracy check of POST /variance/nlresolve against the real 5.5 index + Oracle.

Each case states the table the answer must come from and, when the user named a specific
line item, a word every returned row label must contain. Grading:

  PASS     answered, from an expected table, rows > 0, row filter respected
  ASKED    the route asked a clarification question instead of answering (the expected table
           is among the options -> ASKED_OK, otherwise ASKED_BAD)
  REFUSED  correct for the 'must not answer' cases (unindexed return / nonsense)
  FAIL     answered from the wrong table, wrong rows, no rows, or an HTTP error

Run (VERSION=5.5 in .env):  backend/.venv/Scripts/python.exe scripts/eval_nlresolve_55.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# (query, expected tables (lowercase), substrings every row label must contain, max rows or None)
CASES = [
    # ── CIMS_RAQ ──
    ("Compare industry wise total exposure with the previous quarter", ["cims_raq_q_sec8_ind_brkup"], [], None),
    ("How did the Sugar industry exposure change vs previous quarter", ["cims_raq_q_sec8_ind_brkup"], ["sugar"], None),
    ("Compare the Textiles industry advances for the latest quarter", ["cims_raq_q_sec8_ind_brkup"], ["textile"], None),
    ("Show gross NPA for regulatory retail compared to previous quarter", ["cims_raq_q_sec_12_misc_t4"], ["retail"], None),
    ("Compare gold loan gross advances across categories vs previous quarter", ["cims_raq_q_sec_12_misc_t4"], [], None),
    ("Total export credit disbursed vs previous quarter", ["cims_raq_q_sec7_exp_cr"], [], None),
    ("Compare current and overdue buckets of domestic delinquency vs previous quarter",
     ["cims_raq_q_sec1_part_a_dom", "cims_raq_q_sec1_part_b_dom"], [], None),
    ("Lok Adalat number of cases cumulative compared with previous quarter", ["cims_raq_q_sec4_part_d"], ["lok"], None),
    ("Compare accounts restructured through CDR vs previous quarter", ["cims_raq_q_sec3_part_a", "cims_raq_q_sec3_part_b"], [], None),
    ("Education loans standard advances sectoral credit vs previous quarter",
     ["cims_raq_q_sec8_sec_credit", "cims_raq_q_sec8_sec_credit_1"], ["education"], None),
    ("Top defaulters funded credit exposure compared to previous quarter", ["cims_raq_q_sec5_top_cr_tl"], [], None),
    ("MSME foreign currency exposure compared with previous quarter", ["cims_raq_q_sec10"], [], None),
    ("Held to maturity government securities book value vs previous quarter", ["cims_raq_q_sec6_part_a"], [], None),
    ("Country risk exposure total for the latest quarter vs previous", ["cims_raq_q_sec11"], [], None),
    # ── CIMS_ALE ──
    ("Compare credit protection transactions bought and sold vs previous quarter", ["cims_ale_q_sec2_f2", "cims_ale_q_sec2_f1"], [], None),
    ("Which derivative categories breached PVBP limits vs previous quarter", ["cims_ale_q_sec2_d1"], [], None),
    ("Compare debit and credit entries for accounts with banks in India vs previous quarter", ["cims_ale_q_anx_1_a"], [], None),
    # ── CIMS_ROR ──
    ("Compare Representative Office expenses across all countries for the latest quarter", ["cims_ror_q_sec_c"], ["rep office expense"], None),
    ("Compare total outflows across all countries for the latest quarter", ["cims_ror_q_sec_c"], ["outflow"], None),
    ("Compare net inflows for Singapore vs previous quarter", ["cims_ror_q_sec_c"], ["singapore", "net inflow"], None),
    ("Compare management fees received for Australia for the latest quarter", ["cims_ror_q_sec_c"], ["australia", "management fee"], None),
    ("Show global operations figures for line items in ROR section B vs previous quarter", ["cims_ror_q_sec_b"], [], None),
    ("ROR Part A1 global value vs previous quarter", ["cims_ror_q_sec_a1"], [], None),
    # ── CIMS_LR ──
    ("Compare total outflows in LR Part A2 vs previous quarter", ["cims_lr_part_a2"], ["outflow"], None),
    ("Compare balances with RBI for domestic operations vs previous quarter", ["cims_lr_part_a1"], ["balances with rbi"], None),
    ("Overseas branches total inflows vs previous quarter liquidity", ["cims_lr_part_b1"], [], None),
    ("Compare certificates of deposit for subsidiaries and JVs vs previous quarter", ["cims_lr_part_b2"], ["certificate"], None),
    ("Compare rupee and foreign currency term deposits vs previous quarter", ["cims_lr_trm_dep_amt_wise_tb_2"], [], None),
    ("Export refinance from RBI compared with previous quarter", ["cims_lr_part_a1"], ["export refinance"], None),
    # ── noisy / operation phrasing ──
    ("compre totl outflows LR part a2 with prev quarter", ["cims_lr_part_a2"], [], None),
    ("RAQ industry exposure vs last quarter top 5 increases by percentage", ["cims_raq_q_sec8_ind_brkup"], [], 5),
    ("Show only decreased values in industry wise exposure vs previous quarter", ["cims_raq_q_sec8_ind_brkup"], [], None),
    # ── must NOT answer ──
    ("show borrower details for CRILC", [], [], None),
    ("what is the weather today", [], [], None),
    ("total number of staff for CIMS_XYZ", [], [], None),
]


def _tables_of(options) -> set:
    out = set()
    for o in options or []:
        for key in ("id", "value", "table", "table_name"):
            if isinstance(o, dict) and o.get(key):
                out.add(str(o[key]).lower())
    return out


def grade(q, expected, row_words, max_rows, status, body):
    must_refuse = not expected
    if status != 200:
        return ("REFUSED" if must_refuse else "FAIL"), f"HTTP {status}: {str(body)[:120]}"
    if body.get("needs_clarification"):
        if must_refuse:
            return "REFUSED", f"asked ({body.get('dimension')})"
        opts = _tables_of(body.get("options"))
        ok = bool(opts & set(expected)) or body.get("dimension") in ("date", "return")
        return ("ASKED_OK" if ok else "ASKED_BAD"), f"asked {body.get('dimension')}: {sorted(opts)[:4]}"
    table = str((body.get("provenance") or {}).get("table") or body.get("table_name") or "").lower()
    rows = body.get("rows") or []
    if must_refuse:
        return "FAIL", f"answered from {table} ({len(rows)} rows)"
    if table not in expected:
        return "FAIL", f"wrong table {table} (want {expected})"
    if not rows:
        return "FAIL", "no rows"
    labels = [str(r.get("display_label") or r.get("identifier") or "").lower() for r in rows]
    bad = [l for l in labels if any(w not in l for w in row_words)]
    if row_words and bad:
        return "FAIL", f"{len(bad)}/{len(rows)} rows lack {row_words}, e.g. {bad[0][:60]!r}"
    if max_rows and len(rows) > max_rows:
        return "FAIL", f"{len(rows)} rows > {max_rows}"
    return "PASS", f"{table} rows={len(rows)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    # Eval-only: the per-login NL limit (30/min) would reject a fast batch run.
    os.environ["DV_RATE_LIMIT_NL_PER_MIN"] = "0"
    os.environ["DV_RATE_LIMIT_PER_MIN"] = "0"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    results = []
    for n, (q, expected, words, max_rows) in enumerate(CASES, 1):
        t0 = time.time()
        r = client.post("/variance/nlresolve?loginId=&tenantId=", json={"query": q})
        try:
            body = r.json()
        except Exception:
            body = {"detail": r.text}
        verdict, why = grade(q, expected, words, max_rows, r.status_code, body)
        mode = body.get("resolution_mode") if isinstance(body, dict) else None
        results.append({"n": n, "query": q, "verdict": verdict, "why": why, "mode": mode,
                        "secs": round(time.time() - t0, 1)})
        print(f"{n:>2} {verdict:<9} {results[-1]['secs']:>5}s  {q[:70]}  -> {why}", flush=True)

    answerable = [x for x in results if CASES[x["n"] - 1][1]]
    refuse = [x for x in results if not CASES[x["n"] - 1][1]]
    c = lambda xs, v: sum(1 for x in xs if x["verdict"] == v)
    p, ok, bad, f = c(answerable, "PASS"), c(answerable, "ASKED_OK"), c(answerable, "ASKED_BAD"), c(answerable, "FAIL")
    print("\n── Summary ──")
    print(f"answerable queries : {len(answerable)}")
    print(f"  answered correctly      : {p}  ({p / len(answerable):.0%})  <- strict accuracy")
    print(f"  asked a clarification   : {ok + bad}  (useful {ok}, off-target {bad})")
    print(f"  answered wrongly / error: {f}  ({f / len(answerable):.0%})")
    print(f"  lenient (correct + useful question): {(p + ok) / len(answerable):.0%}")
    print(f"must-refuse queries: {len(refuse)}  correctly refused {c(refuse, 'REFUSED')}  answered anyway {c(refuse, 'FAIL')}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
