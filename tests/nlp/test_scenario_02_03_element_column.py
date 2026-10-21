"""Scenarios 2 & 3 — element-wise and column-wise variation (English prompts, 6.0).

Parser: which elements / columns did the user NAME?
Analysis: are rows narrowed to those elements, columns to those columns, and does an
unknown name produce a "not found" note instead of a silent empty or full table?
"""
from __future__ import annotations

import pytest

from backend.api import nlp as nlp_api
from backend.nlp import schema_info
from backend.nlp.analysis import analyze, resolve_target_columns
from backend.nlp.operation_parser import parse_operation


# ── fixture: one 6.0-shaped table, three elements x three columns ─────────────
COLUMNS = ["ASSETS", "LIABILITIES", "CAPITAL"]
LABELS = {"ASSETS": "Total Assets", "LIABILITIES": "Total Liabilities", "CAPITAL": "Paid-up Capital"}
DATA = {   # element: {col: (prev, curr)}
    "Loans and Advances": {"ASSETS": ("100", "150"), "LIABILITIES": ("50", "40"), "CAPITAL": ("10", "10")},
    "Investments":        {"ASSETS": ("200", "200"), "LIABILITIES": ("80", "90"), "CAPITAL": ("10", "12")},
    "Cash in Hand":       {"ASSETS": ("30", "0"),    "LIABILITIES": ("0", "5"),   "CAPITAL": ("1", "1")},
}


def _result(columns=COLUMNS):
    rows = []
    for label, vals in DATA.items():
        rows.append({
            "identifier": label.upper().replace(" ", "_"), "display_label": label,
            "current": {c: vals[c][1] for c in columns},
            "previous": {"previous_1": {c: {"value": vals[c][0]} for c in columns}},
        })
    return {"columns": list(columns), "display_columns": list(columns), "rows": rows}


def labels(res):
    return [r["display_label"] for r in res["rows"]]


# ── Scenario 2: parser ────────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt,elements", [
    ("Show variation for element X.", ["X"]),
    ("Compare element X between current and previous period.", ["X"]),
    ("What is the percentage change in element X?", ["X"]),
    ("Has element X increased or decreased?", ["X"]),
    ("Show the historical variation of element X.", ["X"]),
    ("Compare Element A and Element B across current and previous periods.", ["A", "B"]),
    ("For the Asset element, show variation across all columns.", ["Asset"]),
    ("Show variation for element Total Loan Assets.", ["Total Loan Assets"]),
    # generic element questions name nothing
    ("Show element-wise variation.", []),
    ("Compare all elements between current and previous periods.", []),
    ("Which elements have changed?", []),
    ("Which element experienced the largest decrease?", []),
    ("Show elements where the value has increased.", []),
])
def test_s2_named_elements(prompt, elements):
    assert parse_operation(prompt).target_elements == elements


# ── Scenario 2: analysis ──────────────────────────────────────────────────────
def test_s2_element_filter_keeps_only_that_element():
    res = analyze(_result(), parse_operation("Show variation for element Investments."))
    assert labels(res) == ["Investments"]
    assert res["analysis"]["counts"]["unchanged"] == 1      # counts cover that element only
    assert res["applied_filters"][0] == "element: Investments"


def test_s2_element_match_is_forgiving():
    res = analyze(_result(), parse_operation("Compare element cash in hand between current and previous period."))
    assert labels(res) == ["Cash in Hand"]
    res = analyze(_result(), parse_operation("Show variation for element Investment."))     # singular
    assert labels(res) == ["Investments"]


def test_s2_two_elements():
    res = analyze(_result(), parse_operation(
        "Compare Element Investments and Element Cash in Hand across current and previous periods."))
    assert labels(res) == ["Investments", "Cash in Hand"]


def test_s2_element_with_direction():
    res = analyze(_result(), parse_operation("Has element Loans and Advances increased?"))
    # only the ASSETS cell rose; the row is kept, matched on that column
    assert labels(res) == ["Loans and Advances"]
    assert res["rows"][0]["analysis"]["matched_columns"] == ["ASSETS"]


def test_s2_unknown_element_says_so():
    res = analyze(_result(), parse_operation("Show variation for element Goodwill."))
    assert res["rows"] == []
    assert "Goodwill" in res["summary"] and "not found" in res["summary"]
    assert any("Goodwill" in n for n in res["analysis_notes"])


def test_s2_generic_element_wise_keeps_everything():
    res = analyze(_result(), parse_operation("Show element-wise variation."))
    assert len(res["rows"]) == 3


# ── Scenario 3: parser ────────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt,columns", [
    ("Show me variation in the Asset column.", ["Asset"]),
    ("Compare Liability column with previous period.", ["Liability"]),
    ("Compare Assets, Liabilities and Capital columns.", ["Assets", "Liabilities", "Capital"]),
    ("Compare all columns between current and previous data.", []),
    ("Show variation for all monetary columns.", []),
    ("Compare these three columns between current and previous periods.", []),
    ("Which column has the highest variation?", []),
    ("Show column-wise variation.", []),
])
def test_s3_named_columns(prompt, columns):
    assert parse_operation(prompt).target_columns == columns


# ── Scenario 3: column matching ───────────────────────────────────────────────
@pytest.mark.parametrize("names,keep,missing", [
    (["Asset"], ["ASSETS"], []),
    (["Liability"], ["LIABILITIES"], []),
    (["Assets", "Liabilities", "Capital"], COLUMNS, []),
    (["paid-up capital"], ["CAPITAL"], []),                 # by schema description
    (["Goodwill"], [], ["Goodwill"]),
    (["Assets", "Goodwill"], ["ASSETS"], ["Goodwill"]),
])
def test_s3_resolve_target_columns(names, keep, missing):
    assert resolve_target_columns(names, COLUMNS, LABELS.get) == (keep, missing)


@pytest.fixture
def described(monkeypatch):
    monkeypatch.setattr(schema_info, "column_description", lambda _t, c: LABELS.get(c))


def test_s3_route_narrows_to_named_columns(described):
    spec = parse_operation("Compare Assets and Capital columns.")
    cols = nlp_api._target_columns(_result(), spec, ["LIABILITIES"], "T")
    assert cols == ["ASSETS", "CAPITAL"]
    out = analyze(nlp_api._restrict_result_columns(_result(), cols), spec)
    assert out["columns"] == ["ASSETS", "CAPITAL"]
    assert [c["column"] for c in out["column_summary"]] == ["ASSETS", "CAPITAL"]


def test_s3_route_unknown_column_keeps_resolver_choice_and_notes_it(described):
    spec = parse_operation("Show me variation in the Goodwill column.")
    cols = nlp_api._target_columns(_result(), spec, ["LIABILITIES"], "T")
    assert cols == ["LIABILITIES"]
    out = analyze(nlp_api._restrict_result_columns(_result(), cols), spec)
    note = next(n for n in out["analysis_notes"] if "Goodwill" in n)
    assert "not found" in note and "Total Assets" in note


def test_s3_column_wise_summary_per_column():
    out = analyze(_result(), parse_operation("Show column-wise variation."))
    by_col = {c["column"]: c for c in out["column_summary"]}
    assert by_col["ASSETS"]["change"] == "20.00"           # 330 -> 350
    assert by_col["CAPITAL"]["increased"] == 1 and by_col["CAPITAL"]["unchanged"] == 2


def test_s3_which_column_highest():
    out = analyze(_result(), parse_operation("Which column has the highest absolute variation?"))
    assert [c["column"] for c in out["column_summary"]] == ["ASSETS"]
