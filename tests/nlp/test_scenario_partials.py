"""Scenario groups that used to be only partly answered: significant changes,
column-level answers, plural rankings, sorting, record-value filters, summaries,
anomalies and duplicates. Everything here runs on hand-computed data, so each
expected value can be checked by reading the fixture."""

from decimal import Decimal

import pytest

from backend.nlp.analysis import FORMULA_TEXT, analyze
from backend.nlp.operation_parser import parse_operation


def _result(data, columns=("AMT",), duplicates=()):
    """data: {label: {col: (prev, curr)}}"""
    rows = [{
        "identifier": k, "display_label": k,
        "current": {c.upper(): v[1] for c, v in cols.items()},
        "previous": {"previous_1": {c: {"value": v[0]} for c, v in cols.items()}},
    } for k, cols in data.items()]
    out = {"columns": list(columns), "rows": rows}
    if duplicates:
        out["duplicate_identifiers"] = list(duplicates)
    return out


ONE = {   # single column AMT: prev, curr
    "A": {"AMT": ("100", "150")},     # +50  / +50%
    "B": {"AMT": ("200", "180")},     # -20  / -10%
    "C": {"AMT": ("300", "300")},     # unchanged
    "D": {"AMT": ("0", "40")},        # new from zero
    "E": {"AMT": ("500", "0")},       # became zero (-100%)
    "F": {"AMT": ("1000", "1005")},   # +5   / +0.5%
    "G": {"AMT": ("20000000", "21000000")},  # +1,000,000 / +5%
}

TWO = {   # two columns
    "A": {"ASSETS": ("100", "120"), "CAPITAL": ("50", "50")},
    "B": {"ASSETS": ("200", "190"), "CAPITAL": ("70", "70")},
    "C": {"ASSETS": ("300", "330"), "CAPITAL": ("10", "10")},
}


def run(q, data=ONE, **kw):
    cols = sorted({c for v in data.values() for c in v})
    return analyze(_result(data, cols, **kw), parse_operation(q))


def ids(res):
    return [r["identifier"] for r in res["rows"]]


# ── significant (P1) ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("q", [
    "Identify significant changes between current and previous data.",
    "Show significant changes.",
    "What changed significantly?",
    "Are there any major increases or decreases?",
])
def test_significant_uses_default_threshold_and_says_so(q):
    res = run(q)
    # > 10% either way: A +50%, E -100%; D is new from zero (undefined %), so not here.
    assert ids(res) == ["A", "E"]
    assert any("Significant" in n and "10%" in n for n in res["analysis_notes"])


def test_significant_with_a_number_uses_the_number():
    spec = parse_operation("Show significant changes above 40%.")
    assert spec.significant is False and spec.threshold.min_value == Decimal("40")


# ── plural rankings (P5) ──────────────────────────────────────────────────────
def test_plural_lowest_returns_several_rows():
    spec = parse_operation("Give me the elements with the lowest variation.")
    assert spec.bottom_n == 10


def test_singular_highest_returns_one():
    assert parse_operation("Which element experienced the largest decrease?").top_n == 1


# ── column level (P2, P4, P6) ─────────────────────────────────────────────────
def test_column_with_maximum_increase_respects_direction():
    data = {"A": {"UP": ("100", "110"), "DOWN": ("100", "10")}}
    res = run("Which column has the maximum increase?", data)
    assert [c["column"] for c in res["column_summary"]] == ["UP"]


def test_column_with_largest_decrease():
    data = {"A": {"UP": ("100", "110"), "DOWN": ("100", "10")}}
    res = run("Which column has the largest decrease?", data)
    assert [c["column"] for c in res["column_summary"]] == ["DOWN"]


def test_columns_with_zero_variation_means_whole_column():
    res = run("Find columns with zero variation.", TWO)
    assert [c["column"] for c in res["column_summary"]] == ["CAPITAL"]
    assert "CAPITAL" in res["summary"]


def test_which_columns_changed_lists_only_moved_columns():
    res = run("Which columns have changed?", TWO)
    assert [c["column"] for c in res["column_summary"]] == ["ASSETS"]


# ── named targets (P3, P8) ────────────────────────────────────────────────────
def test_all_monetary_columns_flag():
    spec = parse_operation("Show variation for all monetary columns.")
    assert spec.all_columns and spec.target_columns == []


def test_element_then_all_columns_parses_cleanly():
    spec = parse_operation("For element X, compare all columns.")
    assert spec.target_elements == ["X"] and spec.target_columns == []


# ── record-value filters (P9) ─────────────────────────────────────────────────
def test_record_amount_filter_applies_to_current_value():
    res = run("Compare only records where amount is greater than ₹10 lakh.")
    assert ids(res) == ["G"]          # only G holds more than 1,000,000


def test_change_threshold_and_record_filter_together():
    spec = parse_operation(
        "Show elements with more than 10% variation where current value is above ₹1 crore.")
    assert spec.threshold.min_value == Decimal("10") and spec.threshold.kind == "pct"
    assert spec.record_filter.basis == "current" and spec.record_filter.min_value == Decimal("10000000")
    # G is above 1 crore but moved only 5%.
    assert ids(analyze(_result(ONE, ["AMT"]), spec)) == []


# ── sorting (G15) ─────────────────────────────────────────────────────────────
def test_sort_by_percentage_keeps_all_rows_highest_first():
    res = run("Sort by percentage variation.")
    assert ids(res)[:2] == ["E", "A"]          # |-100%|, 50%
    assert ids(res)[-1] == "D"                 # undefined % last
    assert len(res["rows"]) == len(ONE)


def test_sort_alphabetically():
    res = run("Sort alphabetically by element.", {"b": {"AMT": ("1", "2")}, "a": {"AMT": ("1", "3")}})
    assert ids(res) == ["a", "b"]


def test_sort_by_current_value():
    res = run("Sort by current value.")
    assert ids(res)[0] == "G"


def test_highest_increases_first():
    res = run("Show highest increases first.")
    assert set(ids(res)) == {"A", "D", "F", "G"}
    assert ids(res)[0] == "A"


# ── summaries (G17/18, G22) ───────────────────────────────────────────────────
def test_executive_summary_has_both_directions():
    res = run("Summarize increases and decreases separately.")
    d = res["summary_detail"]
    assert d["top_increases"] and d["top_decreases"]
    assert "B" in " ".join(d["top_decreases"]) and "A" in " ".join(d["top_increases"])


def test_formula_explanation():
    res = run("How is the percentage change calculated?")
    assert res["formula"] == FORMULA_TEXT


# ── anomaly (G12, G17 "attention") ────────────────────────────────────────────
def test_attention_maps_to_anomaly():
    assert parse_operation("Which numbers require my attention?").operation == "anomaly"


def test_anomaly_names_the_rule_and_flags_outliers():
    data = {f"R{i}": {"AMT": ("100", str(100 + i))} for i in range(10)}   # 0..9%
    data["BIG"] = {"AMT": ("100", "400")}                                   # +300%
    res = run("Find unusual variations.", data)
    assert ids(res) == ["BIG"]
    assert any(n.startswith("Rule:") for n in res["analysis_notes"])


def test_anomaly_with_too_few_values_says_so():
    res = run("Find unusual variations.", {"A": {"AMT": ("1", "2")}})
    assert any("Too few values" in n for n in res["analysis_notes"])


# ── duplicates are not ranked ─────────────────────────────────────────────────
def test_duplicates_excluded_from_ranking():
    res = run("Show top 2 increases.", duplicates=["A"])
    assert "A" not in ids(res)
    assert any("duplicate" in n for n in res["analysis_notes"])


# ── filters on descriptive values (G14) ───────────────────────────────────────
def _branch_result():
    rows = []
    for key, branch, seg, prev, curr in [
        ("L1", "Mumbai", "Retail", "100", "150"),
        ("L2", "Mumbai", "Corporate", "200", "180"),
        ("L3", "Pune", "Retail", "300", "390"),
    ]:
        rows.append({"identifier": key, "display_label": f"{seg} loan {key}",
                     "current": {"BRANCH_NAME": branch, "SEGMENT": seg, "AMT": curr},
                     "previous": {"previous_1": {"AMT": {"value": prev}}}})
    return {"columns": ["AMT"], "rows": rows}


@pytest.mark.parametrize("q,expected", [
    ("Show variation for Mumbai branch.", ["L1", "L2"]),
    ("Compare only retail loans.", ["L1", "L3"]),
    ("Show variations for corporate customers.", ["L2"]),
    ("Show elements with more than 20% variation for Mumbai branch.", ["L1"]),
])
def test_dimension_filters(q, expected):
    assert ids(analyze(_branch_result(), parse_operation(q))) == expected


def test_dimension_value_not_present_is_said():
    res = analyze(_branch_result(), parse_operation("Show variation for Delhi branch."))
    assert res["rows"] == []
    assert any("Delhi" in n for n in res["analysis_notes"])


def test_no_dimension_means_no_slice():
    assert parse_operation("Show variations for this department.").dimension_filters == []


# ── real row labels that contain "and" / "on" (QCB_F010) ──────────────────────
def _f010():
    return _result({
        "Cash and Cash Equivalents": {"TOTAL_LC": ("100", "120")},
        "Cash": {"TOTAL_LC": ("50", "50")},
        "Claims on Central Banks": {"TOTAL_LC": ("200", "150")},
        "Claims on Banks": {"TOTAL_LC": ("300", "310")},
    }, ["TOTAL_LC"])


@pytest.mark.parametrize("q,expected", [
    ("Show variation for element Cash and Cash Equivalents in QCB_F010 inflows vs previous quarter",
     ["Cash and Cash Equivalents"]),
    ("For element Claims on Central Banks, compare all columns in QCB_F010 inflows vs previous quarter",
     ["Claims on Central Banks"]),
    ("Has element Claims on Banks increased or decreased in QCB_F010 inflows vs previous quarter",
     ["Claims on Banks"]),
])
def test_whole_label_with_connector_words(q, expected):
    spec = parse_operation(q)
    assert spec.target_columns == []
    assert ids(analyze(_f010(), spec)) == expected


def test_two_elements_still_split_when_the_phrase_is_not_a_label():
    spec = parse_operation("Compare element Cash and Claims on Banks vs previous quarter")
    assert set(ids(analyze(_f010(), spec))) >= {"Cash"}
