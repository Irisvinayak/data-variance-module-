"""eval_scoped_retrieval.py — grades the RETURN-SCOPED stage of NL resolution.

The question this answers is the one the global harness cannot: *given that we
already know the correct return, do we pick the right table and the right
column inside it?* That is the path taken whenever the user names a return in
the query or answers the "which return?" clarification — and it was, until this
was measured, completely unranked: _shortlist_for_return did not even take the
query as a parameter.

The headline metric is TRUNCATION REACHABILITY. intent_resolver.py shows the
LLM only the first INTENT_MAX_COLS_PER_TABLE columns of each candidate table,
while _validate_grounding accepts any column in the full shortlist. So a
correct column ranked past that cap is not merely deprioritised — the model
never sees it and can never name it. Any non-zero value here is an
unreachable-answer rate.

No Ollama and no Oracle: this only touches the FAISS indices, schema.json and
this app's XML. Run from the project root:

    backend/.venv/Scripts/python.exe scripts/eval_scoped_retrieval.py
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from _bench_cases import build_cases                                # noqa: E402
from backend.nlp import nlp_config as cfg                           # noqa: E402
from backend.nlp import schema_info                                 # noqa: E402

LOGIN_ID = "iris810"        # a real active user in this deployment's XML_User.xml


def _rank_of(names, wanted) -> int | None:
    for i, n in enumerate(names):
        if n in wanted:
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    import inspect
    import logging

    import backend.main as main_mod

    # The XML sweep behind return resolution logs a WARNING per unmappable
    # return (~150 of them) which buries the actual results.
    logging.getLogger("backend.service").setLevel(logging.ERROR)
    logging.getLogger("backend.nlp.return_lookup").setLevel(logging.ERROR)
    logging.getLogger("backend.query_xml_lookup").setLevel(logging.ERROR)

    # Signature-tolerant on purpose: this harness has to grade the BEFORE
    # (where _shortlist_for_return takes only return_id and ignores the query
    # entirely — the defect being measured) and the AFTER (where it takes the
    # query and ranks with it). Detecting which is present means one script
    # produces both numbers, instead of the comparison spanning two checkouts.
    takes_query = len(inspect.signature(main_mod._shortlist_for_return).parameters) > 1
    print(f"[bench] _shortlist_for_return {'RANKS by query' if takes_query else 'IGNORES the query (baseline)'}")

    def shortlist_for(return_id: str, query: str):
        if takes_query:
            return main_mod._shortlist_for_return(return_id, query, LOGIN_ID)
        return main_mod._shortlist_for_return(return_id)

    cases = build_cases(sample=args.sample, seed=args.seed)

    tbl_top1 = tbl_top3 = 0
    col_top1 = col_top3 = 0
    mrr = 0.0
    unreachable = 0            # target column ranked past the LLM's visible window
    nonmetric_first = 0        # a label/date column ranked #1 among a table's columns
    graded_cols = 0
    conf_by_correct = {True: [], False: []}
    autoproceed = collections.Counter()
    elapsed = 0.0
    misses = []

    for case in cases:
        return_id = sorted(case.return_ids)[0]
        start = time.perf_counter()
        shortlist = shortlist_for(return_id, case.query)
        elapsed += time.perf_counter() - start
        if shortlist is None or not shortlist["tables"]:
            misses.append((case.query, "NO-SHORTLIST", ""))
            continue

        ranked_tables = [t["table"].lower() for t in shortlist["tables"]]
        wanted_tables = {t.lower() for t in case.tables}
        t_rank = _rank_of(ranked_tables, wanted_tables)
        tbl_top1 += int(t_rank == 0)
        tbl_top3 += int(t_rank is not None and t_rank < 3)

        # Column ranking is graded WITHIN the correct table only — a wrong
        # table is already counted above, and mixing the two would hide which
        # stage actually failed.
        target_table = next((t for t in ranked_tables if t in wanted_tables), None)
        if target_table:
            cols = [c["column"].lower() for c in shortlist["columns"]
                    if c["table"].lower() == target_table]
            c_rank = _rank_of(cols, case.columns)
            if c_rank is not None:
                graded_cols += 1
                col_top1 += int(c_rank == 0)
                col_top3 += int(c_rank < 3)
                mrr += 1.0 / (c_rank + 1)
                if c_rank >= cfg.INTENT_MAX_COLS_PER_TABLE:
                    unreachable += 1
                    if args.verbose:
                        print(f"  UNREACHABLE rank={c_rank} {case.query!r} "
                              f"-> {target_table}.{sorted(case.columns)[0]}")
                if cols and not schema_info.is_metric_column(target_table, cols[0]):
                    nonmetric_first += 1

        conf = shortlist.get("table_confidence", 0.0)
        conf_by_correct[t_rank == 0].append(conf)
        if conf < cfg.CONFIDENCE_ASK_FLOOR:
            autoproceed["ASK-return"] += 1
        elif shortlist.get("table_ambiguous") or conf < cfg.CONFIDENCE_AUTO_PROCEED:
            autoproceed["ASK-table"] += 1
        else:
            autoproceed["AUTO"] += 1

        if t_rank != 0:
            misses.append((case.query, ranked_tables[0], sorted(wanted_tables)[0]))

    n = len(cases)
    mean = lambda v: sum(v) / len(v) if v else 0.0
    print()
    print(f"cases                        {n}   ({1000*elapsed/max(n,1):.1f} ms/case)")
    print(f"scoped TABLE  top-1          {tbl_top1}/{n} ({100*tbl_top1/n:.0f}%)")
    print(f"scoped TABLE  top-3          {tbl_top3}/{n} ({100*tbl_top3/n:.0f}%)")
    if graded_cols:
        print(f"scoped COLUMN top-1          {col_top1}/{graded_cols} ({100*col_top1/graded_cols:.0f}%)")
        print(f"scoped COLUMN top-3          {col_top3}/{graded_cols} ({100*col_top3/graded_cols:.0f}%)")
        print(f"scoped COLUMN MRR            {mrr/graded_cols:.3f}")
        print(f"UNREACHABLE (past cap {cfg.INTENT_MAX_COLS_PER_TABLE:>2})   "
              f"{unreachable}/{graded_cols} ({100*unreachable/graded_cols:.0f}%)"
              "   <- answers the LLM can never name")
        print(f"non-metric column ranked #1  {nonmetric_first}/{graded_cols} "
              f"({100*nonmetric_first/graded_cols:.0f}%)   <- label/date beating a measure")
    print(f"confidence  mean(correct)    {mean(conf_by_correct[True]):.3f}")
    print(f"confidence  mean(wrong)      {mean(conf_by_correct[False]):.3f}"
          f"   separation={mean(conf_by_correct[True])-mean(conf_by_correct[False]):+.3f}")
    print(f"gate outcomes                {dict(autoproceed)}")

    if misses and args.verbose:
        print(f"\n{len(misses)} table miss(es), first 10:")
        for q, got, want in misses[:10]:
            print(f"  {q[:52]:<52} got={got[:30]:<30} want={want}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
