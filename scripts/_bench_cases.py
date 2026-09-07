"""_bench_cases.py — shared ground truth for the retrieval benchmarks.

Both scripts/eval_retrieval.py (global path) and
scripts/eval_scoped_retrieval.py (return-scoped path) grade against this, so
neither can be tuned on a different set than the other.

Where the ground truth comes from: every record in column_meta.pkl carries a
"text" field shaped

    "<table>.<column> | <column words> | <HUMAN DESCRIPTION> | <ReturnName> | <table blurb>"

The human description is business language a user could plausibly type ("Debit
Entries Amount"), and it is NOT the column identifier ("db_amt") — so grading a
retrieval against it is a real test, not string matching. Each case is
therefore: query = the description, expected = the (table, column) that
description belongs to, plus the return that owns the table.

Cases are dropped when they cannot be graded fairly:
  * blank description, or a description equal to the column name itself;
  * a description that appears on two DIFFERENT columns of the SAME table —
    no retriever could be expected to pick between them, so counting it as a
    miss would just add noise to every measurement.
A description shared across DIFFERENT tables is kept, with every owning table
accepted as correct — that is genuine, gradeable ambiguity.
"""

from __future__ import annotations

import collections
import os
import pickle
import random
import sys
from typing import Dict, List, NamedTuple, Set, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.nlp import nlp_config as cfg          # noqa: E402
from backend.nlp import return_lookup              # noqa: E402


class BenchCase(NamedTuple):
    query: str                  # the human description, used as the query
    tables: Set[str]            # every table carrying a column with this description
    columns: Set[str]           # the column name(s) it maps to (lowercase)
    return_ids: Set[str]        # returns owning those tables


def _description_of(text: str) -> str:
    parts = [p.strip() for p in (text or "").split("|")]
    return parts[2] if len(parts) >= 3 else ""


def build_cases(sample: int | None = 150, seed: int = 7) -> List[BenchCase]:
    """Deterministically sampled cases. `sample=None` for the full corpus."""
    with open(cfg.COLUMN_META_PATH, "rb") as fh:
        records = pickle.load(fh)

    # description -> {table -> {columns}}
    by_desc: Dict[str, Dict[str, Set[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    texts: Dict[str, str] = {}
    for rec in records:
        desc = _description_of(rec.get("text", ""))
        table, column = rec.get("table"), rec.get("column")
        if not desc or not table or not column:
            continue
        if desc.strip().lower() == column.strip().lower():
            continue                       # degenerate: description IS the identifier
        by_desc[desc][table].add(column.lower())
        texts.setdefault(table, rec.get("text", ""))

    cases: List[BenchCase] = []
    dropped_ambiguous = 0
    for desc, tables in by_desc.items():
        if any(len(cols) > 1 for cols in tables.values()):
            dropped_ambiguous += 1         # same description on 2 columns of one table
            continue
        return_ids = set()
        for table in tables:
            ret = return_lookup.get_return_for_table(table, hint_text=texts.get(table))
            if ret and ret.get("return_id"):
                return_ids.add(str(ret["return_id"]))
        if not return_ids:
            continue                       # unreachable by the pipeline anyway
        cases.append(BenchCase(
            query=desc,
            tables=set(tables),
            columns={c for cols in tables.values() for c in cols},
            return_ids=return_ids,
        ))

    cases.sort(key=lambda c: c.query)      # stable before sampling
    if sample is not None and sample < len(cases):
        random.Random(seed).shuffle(cases)
        cases = cases[:sample]
    cases.sort(key=lambda c: c.query)

    print(f"[bench] {len(cases)} case(s); dropped {dropped_ambiguous} ungradeable "
          f"(one description on several columns of the same table)")
    return cases
