"""One-figure answers (backend/nlp/point_value.py) and the no-rows retry refill.

The rule under test: narrow to one measure + one line item ONLY when it can be proven;
otherwise return the result unchanged. Every number on the card must be the one
compute_variance produced (built here with the real _column_metrics).
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.data.calculate_variance import _column_metrics  # noqa: E402
from backend.nlp import point_value as pv  # noqa: E402
from backend.nlp.operation_parser import parse_operation  # noqa: E402

DESC = {"TOTAL_LOAN_ASSETS": "Total Loan Assets", "NPA_AMOUNT": "Non Performing Asset Amount"}


def describe(col):
    return DESC.get(col)


def _row(label, cur, prev, cols=("TOTAL_LOAN_ASSETS",)):
    curr_row = {"DESCRIPTION": label, **{c: v for c, v in zip(cols, cur)}}
    out = {"identifier": label, "display_label": label, "current": curr_row, "previous": {}}
    if prev is not None:
        prev_row = {"DESCRIPTION": label, **{c: v for c, v in zip(cols, prev)}}
        out["previous"]["previous_1"] = _column_metrics(list(cols), prev_row, curr_row)
    return out


def _computed(rows, cols=("TOTAL_LOAN_ASSETS",), periods=("31-DEC-2024",), **extra):
    return {"table_name": "T", "reporting_date": "31-MAR-2025",
            "comparison_periods": list(periods), "columns": list(cols),
            "display_columns": ["DESCRIPTION", *cols], "rows": rows, **extra}


ROWS = [
    _row("(i) Cash", [100], [90]),
    _row("(iii) Term Loans", [1234.5], [1100]),
    _row("Term Loans - Overdue", [50], [40]),
    _row("Investments", [700], [700]),
]


def run(computed, query, metric, labels=None):
    return pv.apply_value_lookup(computed, query=query, metric_text=metric, table_name="T",
                                 describe=describe, matched_labels=labels or [])


# ── intent ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q", [
    "what is total loan assets for term loans",
    "What was the value of cash in CIMS_RAQ",
    "how much is investments as of 31-Mar-2025",
    "total loan assets for term loans",                 # no phrase: still a plain compare
])
def test_lookup_applies_to_plain_questions(q):
    assert pv.lookup_applies(parse_operation(q), q)


@pytest.mark.parametrize("q", [
    "top 5 items by change in total loan assets",
    "which items increased by more than 10%",
    "show all rows for total loan assets",
    "show the full table",
    "trend of total loan assets over last 4 quarters",
    "summarize the changes",
])
def test_lookup_off_for_table_questions(q):
    assert not pv.lookup_applies(parse_operation(q), q)


# ── one line item ─────────────────────────────────────────────────────────────
def test_label_with_numbering_prefix_is_matched_and_numbers_are_compute_variance_s():
    out, notes = run(_computed(list(ROWS)), "what is total loan assets for term loans",
                     "total loan assets for term loans")
    card = out["value_card"]
    assert out["view"] == "value" and len(out["rows"]) == 1
    assert card["item"] == "(iii) Term Loans" and card["measure"] == "Total Loan Assets"
    assert card["current"] == {"date": "31-MAR-2025", "value": 1234.5}
    assert card["previous"] == {"date": "31-DEC-2024", "value": 1100}
    assert card["change"] == "134.50" and card["pct_change"] == "12.23%"
    assert card["direction"] == "up" and card["note"] is None and notes == []


def test_retriever_label_typed_by_user_wins():
    labels = [{"table": "T", "column": "DESCRIPTION", "value": "Term Loans - Overdue"}]
    out, _ = run(_computed(list(ROWS)), "what is total loan assets for term loans overdue",
                 "total loan assets for term loans overdue", labels)
    assert out["value_card"]["item"] == "Term Loans - Overdue"


def test_label_of_another_table_is_ignored():
    labels = [{"table": "OTHER", "column": "DESCRIPTION", "value": "Cash"}]
    out, _ = run(_computed(list(ROWS)), "what is total loan assets", "total loan assets", labels)
    assert "value_card" not in out and len(out["rows"]) == 4


def test_ambiguous_item_is_not_guessed():
    rows = [_row("Term Loans Domestic", [1], [1]), _row("Term Loans Overseas", [2], [2])]
    comp = _computed(rows)
    out, notes = run(comp, "what is term loans", "term loans")
    assert out is comp and "value_card" not in out
    assert notes and "2 line items match" in notes[0]


def test_no_item_words_keeps_table():
    comp = _computed(list(ROWS))
    out, notes = run(comp, "what is total loan assets", "total loan assets")
    assert out is comp and notes == []


def test_single_row_table_gives_card():
    out, _ = run(_computed([_row("Total", [5], [4])]), "total loan assets", "total loan assets")
    assert out["value_card"]["current"]["value"] == 5


# ── edge cases ────────────────────────────────────────────────────────────────
def test_previous_zero_pct_is_na():
    out, _ = run(_computed([_row("Cash", [10], [0])]), "what is cash", "cash")
    assert out["value_card"]["pct_change"] is None and out["value_card"]["direction"] == "up"


def test_item_missing_in_previous_period():
    out, notes = run(_computed([_row("Cash", [10], None), _row("Gold", [1], [1])]), "what is cash", "cash")
    card = out["value_card"]
    assert card["previous"]["value"] is None and "not reported on 31-DEC-2024" in card["note"]
    assert notes == [card["note"]]


def test_no_earlier_period():
    out, _ = run(_computed([_row("Cash", [10], None)], periods=()), "what is cash", "cash")
    assert "no earlier period" in out["value_card"]["note"]


def test_item_missing_in_current_period_uses_removed_row():
    removed = [{"identifier": "Gold", "display_label": "Gold", "period": "previous_1",
                "previous": {"DESCRIPTION": "Gold", "TOTAL_LOAN_ASSETS": 33}}]
    comp = _computed([_row("Cash", [10], [9]), _row("Silver", [1], [1])],
                     removed_rows=removed, removed_notice="1 row(s) ...")
    out, _ = run(comp, "what is gold", "gold")
    card = out["value_card"]
    assert card["current"]["value"] is None and card["previous"]["value"] == 33
    assert out["rows"] == [] and "removed_notice" not in out


def test_duplicate_key_is_not_collapsed():
    comp = _computed(list(ROWS), duplicate_identifiers=["(iii) Term Loans"])
    out, notes = run(comp, "what is term loans", "term loans")
    assert out is comp and "more than once" in notes[0]


def test_several_periods_keep_table():
    comp = _computed([_row("Cash", [1], [1])], periods=("31-DEC-2024", "30-SEP-2024"))
    out, _ = run(comp, "what is cash", "cash")
    assert out is comp


def test_two_measures_keep_table_with_note():
    comp = _computed([_row("Cash", [1, 2], [1, 2], cols=("TOTAL_LOAN_ASSETS", "NPA_AMOUNT"))],
                     cols=("TOTAL_LOAN_ASSETS", "NPA_AMOUNT"))
    out, notes = run(comp, "what is cash", "cash")
    assert out is comp and "More than one measure" in notes[0]


# ── measure pick ──────────────────────────────────────────────────────────────
def test_pick_column_unique_best():
    assert pv.pick_column(["TOTAL_LOAN_ASSETS", "NPA_AMOUNT"], "non performing asset amount", describe) == "NPA_AMOUNT"


def test_pick_column_tie_is_none():
    assert pv.pick_column(["TOTAL_LOAN_ASSETS", "NPA_AMOUNT"], "asset", describe) is None


# ── retry refill (G1) ─────────────────────────────────────────────────────────
def test_retry_keeps_a_previous_period(monkeypatch):
    from backend.api import nlp as api_nlp
    from backend.nlp import date_resolver
    cands = [date(2025, 3, 31), date(2024, 12, 31), date(2024, 9, 30)]
    monkeypatch.setattr(date_resolver, "_candidate_dates", lambda *a, **k: cands)
    out = api_nlp._refill_retry_dates(["31-MAR-2025", "31-DEC-2024"], "2041", "T", "RDATE", None)
    assert out == ["31-DEC-2024", "30-SEP-2024"]


def test_retry_with_no_older_date_is_unchanged(monkeypatch):
    from backend.api import nlp as api_nlp
    from backend.nlp import date_resolver
    monkeypatch.setattr(date_resolver, "_candidate_dates",
                        lambda *a, **k: [date(2025, 3, 31), date(2024, 12, 31)])
    out = api_nlp._refill_retry_dates(["31-MAR-2025", "31-DEC-2024"], "2041", "T", "RDATE", None)
    assert out == ["31-DEC-2024"]
