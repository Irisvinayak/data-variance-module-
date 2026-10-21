"""Every prompt in the NLP requirement (12 use-case groups), run through the parser.

One row per prompt: (prompt, expected spec attributes). Attribute names may be dotted
for the threshold ("threshold.min_value"). Prompts the product has not implemented yet
are listed in GAPS as strict xfails, so the day one is built the test flips and says so.
"""
from __future__ import annotations

from decimal import Decimal as Dz

import pytest

from backend.nlp.operation_parser import parse_operation

NOOP = {"operation": "compare", "direction": "either"}


def _get(spec, dotted):
    obj = spec
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def check(prompt, expected):
    spec = parse_operation(prompt)
    for attr, want in expected.items():
        assert _get(spec, attr) == want, f"{prompt!r}: {attr}={_get(spec, attr)!r}, wanted {want!r}"


# ── 1. Basic current vs previous ──────────────────────────────────────────────
GROUP1 = [
    "Compare the current data with the previous period.",
    "Show me the variation between current and previous data.",
    "Perform data variation analysis for the latest two periods.",
    "Compare this month’s data with last month’s data.",
    "Compare current quarter data with previous quarter.",
    "Compare current year data with previous year.",
    "What has changed between the current and previous reporting period?",
    "Show all changes between the two periods.",
    "Give me a summary of the changes.",
]


@pytest.mark.parametrize("prompt", GROUP1)
def test_g1_basic_comparison_keeps_the_plain_table(prompt):
    spec = parse_operation(prompt)
    assert spec.operation in ("compare", "filter")      # "what has changed" may drop unchanged rows
    assert spec.threshold is None and spec.top_n is None and spec.bottom_n is None


# ── 2. Element-wise ───────────────────────────────────────────────────────────
GROUP2 = [
    ("Show element-wise variation.", {**NOOP, "group_by": "element"}),
    ("Compare all elements between current and previous periods.", NOOP),
    ("Show elements where the value has increased.", {"operation": "filter", "direction": "increase"}),
    ("Show elements where the value has decreased.", {"operation": "filter", "direction": "decrease"}),
    ("Identify elements with the highest variation.", {"operation": "rank", "top_n": 10, "measure": "ambiguous"}),
    ("Which elements have no variation?", {"operation": "unchanged"}),
    ("Give me element-wise percentage variation.", {"measure": "pct", "group_by": "element"}),
    ("Show absolute variation for each element.", {"measure": "abs"}),
    ("What is the percentage change in element X?", {"measure": "pct"}),
    ("Has element X increased or decreased?", {"direction": "either"}),
]


@pytest.mark.parametrize("prompt,expected", GROUP2)
def test_g2_element_wise(prompt, expected):
    check(prompt, expected)


def test_g2_which_elements_changed_drops_unchanged():
    assert parse_operation("Which elements have changed?").operation == "filter"


# ── 3. Column-wise ────────────────────────────────────────────────────────────
GROUP3 = [
    ("Show column-wise variation.", {"group_by": "column"}),
    ("Compare all columns between current and previous data.", NOOP),
    ("Show me variation in the Asset column.", NOOP),
    ("Compare Liability column with previous period.", NOOP),
    ("Which column has the highest variation?", {"group_by": "column", "operation": "rank", "top_n": 1}),
    ("Show percentage variation column-wise.", {"group_by": "column", "measure": "pct"}),
    ("Show absolute variation column-wise.", {"group_by": "column", "measure": "abs"}),
    ("Compare Assets, Liabilities and Capital columns.", NOOP),
]


@pytest.mark.parametrize("prompt,expected", GROUP3)
def test_g3_column_wise(prompt, expected):
    check(prompt, expected)


def test_g3_which_columns_changed():
    spec = parse_operation("Which columns have changed?")
    assert spec.group_by == "column" and spec.operation == "filter"


# ── 4. Absolute vs percentage ─────────────────────────────────────────────────
GROUP4 = [
    ("Show absolute variation.", {"measure": "abs"}),
    ("What is the difference between current and previous value?", {"measure": "abs"}),
    ("Which element has the highest absolute increase?",
     {"measure": "abs", "direction": "increase", "top_n": 1, "operation": "rank"}),
    ("Show percentage variation.", {"measure": "pct"}),
    ("Calculate percentage change.", {"measure": "pct"}),
    ("Which elements have changed by more than 10%?", {"measure": "pct", "threshold.min_value": Dz(10)}),
    ("Show elements with variation greater than 20%.", {"measure": "pct", "threshold.min_value": Dz(20)}),
    ("Find elements with more than 50% increase.",
     {"direction": "increase", "threshold.min_value": Dz(50), "measure": "pct"}),
    ("Show elements where the value decreased by more than 10%.",
     {"direction": "decrease", "threshold.min_value": Dz(10), "measure": "pct"}),
]


@pytest.mark.parametrize("prompt,expected", GROUP4)
def test_g4_absolute_vs_percentage(prompt, expected):
    check(prompt, expected)


# ── 5. Increase / decrease ────────────────────────────────────────────────────
GROUP5 = [
    ("Show only increased values.", {"direction": "increase", "operation": "filter"}),
    ("Show only decreased values.", {"direction": "decrease", "operation": "filter"}),
    ("Which elements increased?", {"direction": "increase"}),
    ("Which elements decreased?", {"direction": "decrease"}),
    ("Give me the top 10 increases.", {"top_n": 10, "direction": "increase", "operation": "rank"}),
    ("Give me the top 10 decreases.", {"top_n": 10, "direction": "decrease", "operation": "rank"}),
    ("Which column has the maximum increase?",
     {"top_n": 1, "direction": "increase", "group_by": "column"}),
    ("Which element experienced the largest decrease?", {"top_n": 1, "direction": "decrease"}),
    ("Show all positive variations.", {"direction": "increase"}),
    ("Show all negative variations.", {"direction": "decrease"}),
]


@pytest.mark.parametrize("prompt,expected", GROUP5)
def test_g5_increase_decrease(prompt, expected):
    check(prompt, expected)


# ── 6. Threshold ──────────────────────────────────────────────────────────────
GROUP6 = [
    ("Show variations greater than 10%.", {"threshold.min_value": Dz(10), "measure": "pct"}),
    ("Find elements where variation exceeds 20%.", {"threshold.min_value": Dz(20)}),
    ("Show changes above ₹10 lakh.", {"threshold.min_value": Dz(1_000_000), "measure": "abs"}),
    ("Show elements with absolute variation greater than 5 million.",
     {"threshold.min_value": Dz(5_000_000), "measure": "abs"}),
    ("Identify changes below -15%.", {"threshold.max_value": Dz(-15), "threshold.signed": True}),
    ("Show variations between 10% and 30%.",
     {"threshold.min_value": Dz(10), "threshold.max_value": Dz(30)}),
    ("Show elements where variation is more than 25% in either direction.",
     {"threshold.min_value": Dz(25), "direction": "either"}),
    ("Show changes above 2 crore.", {"threshold.min_value": Dz(20_000_000), "measure": "abs"}),
]


@pytest.mark.parametrize("prompt,expected", GROUP6)
def test_g6_threshold(prompt, expected):
    check(prompt, expected)


# ── 7. Top / bottom ───────────────────────────────────────────────────────────
GROUP7 = [
    ("Show top 5 elements with highest variation.", {"top_n": 5, "operation": "rank", "measure": "ambiguous"}),
    ("Show top 10 increases.", {"top_n": 10}),
    ("Show top 10 decreases.", {"top_n": 10, "direction": "decrease"}),
    ("Which are the five most significant changes?", {"top_n": 5, "operation": "rank"}),
    ("Give me the elements with the lowest variation.", {"bottom_n": 10, "operation": "rank"}),
    ("Rank elements based on percentage variation.", {"operation": "rank", "measure": "pct"}),
    ("Rank columns based on absolute variation.", {"operation": "rank", "measure": "abs", "group_by": "column"}),
]


@pytest.mark.parametrize("prompt,expected", GROUP7)
def test_g7_top_bottom(prompt, expected):
    check(prompt, expected)


def test_g7_ambiguous_measure_is_flagged_so_the_caller_can_ask():
    assert parse_operation("Show top 5 elements with highest variation.").measure == "ambiguous"
    assert parse_operation("Show top 5 elements with highest percentage variation.").measure == "pct"


# ── 8. Zero / no variation ────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt", [
    "Show elements with no change.",
    "Which elements have remained unchanged?",
    "Show elements where current and previous values are identical.",
    "Find columns with zero variation.",
    "Are there any elements with no movement?",
    "Show unchanged values.",
])
def test_g8_zero_variation(prompt):
    assert parse_operation(prompt).operation == "unchanged"


# ── 9. New / removed values ───────────────────────────────────────────────────
@pytest.mark.parametrize("prompt,kind", [
    ("Show newly populated elements.", "new"),
    ("Which elements have values in current period but not in previous period?", "new"),
    ("Which elements became zero?", "zero"),
    ("Which elements had values previously but are zero now?", "zero"),
    ("Identify newly reported values.", "new"),
    ("Identify elements that disappeared from the current period.", "zero"),
    ("Show elements where previous value was zero and current value is non-zero.", "new"),
])
def test_g9_new_removed(prompt, kind):
    spec = parse_operation(prompt)
    assert (spec.operation, spec.new_removed) == ("new_removed", kind)


# ── 11. Historical / multi-period ─────────────────────────────────────────────
@pytest.mark.parametrize("prompt", [
    "Show variation of this element for the last 6 months.",
    "Show the trend of Assets for the last 12 months.",
    "Compare the last four quarters.",
    "Which elements have continuously increased over the last 5 periods?",
    "Show month-on-month variation.",
    "Show quarter-on-quarter variation.",
    "Show year-on-year variation.",
])
def test_g11_trend_is_recognised(prompt):
    assert parse_operation(prompt).operation == "trend"


@pytest.mark.parametrize("prompt,gran", [
    ("Show month-on-month variation.", "mom"),
    ("Show quarter-on-quarter variation.", "qoq"),
    ("Show year-on-year variation.", "yoy"),
])
def test_g11_granularity(prompt, gran):
    assert parse_operation(prompt).granularity == gran


# ── 12. Outlier / anomaly ─────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt", [
    "Find unusual variations.",
    "Identify abnormal changes.",
    "Show significant deviations from the previous period.",
    "Identify potential outliers.",
    "Which values need my attention?",
    "Highlight unusual changes.",
    "Show me the exceptions in this report.",
    "Identify the top anomalies based on variation.",
])
def test_g12_anomaly_is_recognised(prompt):
    assert parse_operation(prompt).operation == "anomaly"


# ── Known gaps (strict xfail: flips to a failure the day they are built) ──────
GAPS = [
    # Hinglish (Phase 6)
    ("10% se zyada variation wale elements dikhao.", {"threshold.min_value": Dz(10)}),
    ("20 lakh se zyada change kaha hua hai?", {"threshold.min_value": Dz(2_000_000)}),
    ("Jaha decrease 15% se zyada hai wo dikhao.", {"direction": "decrease", "threshold.min_value": Dz(15)}),
]


@pytest.mark.parametrize("prompt,expected", GAPS)
@pytest.mark.xfail(strict=True, reason="Hinglish is deferred; English only for now")
def test_known_gaps(prompt, expected):
    check(prompt, expected)


# ── Named element / column targets (formerly gaps) ────────────────────────────
@pytest.mark.parametrize("prompt,expected", [
    ("Show variation for element X.", {"target_elements": ["X"]}),
    ("Compare element X between current and previous period.", {"target_elements": ["X"]}),
    ("Compare Assets, Liabilities and Capital columns.", {"target_columns": ["Assets", "Liabilities", "Capital"]}),
])
def test_named_targets(prompt, expected):
    check(prompt, expected)
