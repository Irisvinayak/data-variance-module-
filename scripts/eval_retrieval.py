"""
eval_retrieval.py — fast, no-LLM, no-Oracle retrieval accuracy check.

get_relevant_schema() only touches the FAISS/BM25 indices in backend/output/
and this app's own XML config (Returns.xml, table-mapping files, XML_User.xml)
for return_id/auth resolution — no Ollama call, no Oracle connection. That
makes it cheap enough to run after every embedding rebuild or retriever.py
change, to catch a retrieval-quality regression before it ever reaches an
LLM call or a real query.

This is the instrument the scaling plan calls for: TOP_K_*/MIN_*_SCORE in
nlp_config.py were tuned against today's small (~51 table) corpus. Guessing
new values once the real production corpus lands would just trade one set of
untested magic numbers for another — this script is what lets those numbers
be re-tuned against measurements instead of guesses.

Run from the project root:
    backend/.venv/Scripts/python.exe scripts/eval_retrieval.py
"""

from __future__ import annotations

import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Each case: (query, expected_table, login_id). expected_table is matched
# case-insensitively against the ranked "tables" list get_relevant_schema()
# returns. login_id only matters when DV_AUTH_ENABLED=true; "iris810" is a
# real active user in this deployment's XML_User.xml either way.
CASES = [
    ("What is the total gross NPA for the quarter", "cims_raq_q_sec8_sec_credit", "iris810"),
    (
        "provision made for possible losses on doubtful advances, comparing domestic and overseas operations",
        "cims_raq_q_sec2_part_a",
        "iris810",
    ),
    ("SEC2_PART_A risk category standard provision", "cims_raq_q_sec2_part_a", "iris810"),
    ("total loan assets domestic", "cims_raq_q_sec1_part_a_dom", "iris810"),
    ("infra sector credit breakup", "cims_raq_q_sec8_infra_brkup", "iris810"),
    ("sensitivity to securities part b overseas", "cims_raq_q_sec9_sensec_partb", "iris810"),
]


def main() -> int:
    from backend.nlp.retriever import get_relevant_schema

    top1_hits = 0
    topk_hits = 0
    total_elapsed = 0.0
    failures = []

    for query, expected_table, login_id in CASES:
        start = time.perf_counter()
        result = get_relevant_schema(query, login_id)
        elapsed = time.perf_counter() - start
        total_elapsed += elapsed

        ranked = [t["table"].lower() for t in result["tables"]]
        expected = expected_table.lower()

        is_top1 = bool(ranked) and ranked[0] == expected
        is_topk = expected in ranked

        top1_hits += int(is_top1)
        topk_hits += int(is_topk)

        status = "TOP1" if is_top1 else ("TOPK" if is_topk else "MISS")
        if status == "MISS":
            failures.append((query, expected_table, ranked))

        print(f"[{status:4}] {elapsed*1000:6.1f}ms  {query!r}")
        print(f"        expected={expected_table!r}  got={ranked}")

    n = len(CASES)
    print()
    print(f"top-1 accuracy: {top1_hits}/{n} ({100*top1_hits/n:.0f}%)")
    print(f"top-k accuracy: {topk_hits}/{n} ({100*topk_hits/n:.0f}%)")
    print(f"total elapsed:  {total_elapsed:.2f}s  (avg {1000*total_elapsed/n:.1f}ms/query)")

    if failures:
        print()
        print(f"{len(failures)} MISS(ES):")
        for query, expected, ranked in failures:
            print(f"  - {query!r} -> expected {expected!r}, got {ranked}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
