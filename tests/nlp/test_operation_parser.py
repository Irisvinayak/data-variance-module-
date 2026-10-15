from decimal import Decimal

import pytest

from backend.nlp.number_units import parse_amount
from backend.nlp.operation_parser import parse_operation


@pytest.mark.parametrize("text,expected", [
    ("10%", (Decimal("10"), "pct")),
    ("5 million", (Decimal("5000000"), "abs")),
    ("10 lakh", (Decimal("1000000"), "abs")),
    ("₹2 crore", (Decimal("20000000"), "abs")),
    ("1 cr", (Decimal("10000000"), "abs")),
    ("1,500", (Decimal("1500"), "unknown")),
    ("-15%", (Decimal("-15"), "pct")),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("q", [
    "Compare the current data with the previous period.",
    "Compare current quarter data with previous quarter.",
    "Give me a summary of the changes.",
    "Compare March 2026 with February 2026.",
])
def test_plain_compare_is_noop(q):
    assert parse_operation(q).is_noop


def test_direction_only_filter():
    s = parse_operation("Show elements where the value has increased.")
    assert (s.operation, s.direction) == ("filter", "increase")
    s = parse_operation("Show only decreased values.")
    assert (s.operation, s.direction) == ("filter", "decrease")


def test_pct_threshold():
    s = parse_operation("Show variations greater than 10%.")
    assert s.operation == "filter" and s.measure == "pct"
    assert s.threshold.min_value == 10 and not s.threshold.signed


def test_direction_with_threshold():
    s = parse_operation("Show elements where the value decreased by more than 10%.")
    assert s.direction == "decrease" and s.threshold.min_value == 10 and s.measure == "pct"
    s = parse_operation("Find elements with more than 50% increase.")
    assert s.direction == "increase" and s.threshold.min_value == 50


def test_abs_threshold_units():
    s = parse_operation("Show changes above ₹10 lakh.")
    assert s.measure == "abs" and s.threshold.min_value == Decimal("1000000")
    s = parse_operation("Show elements with absolute variation greater than 5 million.")
    assert s.measure == "abs" and s.threshold.min_value == Decimal("5000000")


def test_negative_threshold_is_signed():
    s = parse_operation("Identify changes below -15%.")
    assert s.threshold.signed and s.threshold.max_value == Decimal("-15")


def test_between():
    s = parse_operation("Show variations between 10% and 30%.")
    assert (s.threshold.min_value, s.threshold.max_value) == (10, 30)
    s = parse_operation("Show variations between 10 and 30%.")
    assert s.threshold.kind == "pct"


def test_either_direction():
    s = parse_operation("Show elements where variation is more than 25% in either direction.")
    assert s.direction == "either" and s.threshold.min_value == 25


@pytest.mark.parametrize("q,top,bottom", [
    ("Show top 5 elements with highest variation.", 5, None),
    ("Give me the top 10 increases.", 10, None),
    ("Which are the five most significant changes?", 5, None),
    ("Give me the bottom 3 elements.", None, 3),
])
def test_top_bottom_n(q, top, bottom):
    s = parse_operation(q)
    assert (s.operation, s.top_n, s.bottom_n) == ("rank", top, bottom)


def test_highest_variation_is_ambiguous_but_absolute_increase_is_not():
    assert parse_operation("Show top 5 elements with highest variation.").measure == "ambiguous"
    s = parse_operation("Which element has the highest absolute increase?")
    assert s.measure == "abs" and s.direction == "increase" and s.top_n == 1


def test_column_wise_rank():
    s = parse_operation("Which column has the highest variation?")
    assert s.group_by == "column" and s.operation == "rank"


@pytest.mark.parametrize("q", [
    "Show elements with no change.",
    "Which elements have remained unchanged?",
    "Find columns with zero variation.",
    "Show unchanged values.",
])
def test_unchanged(q):
    assert parse_operation(q).operation == "unchanged"


def test_new_and_removed():
    s = parse_operation("Show elements where previous value was zero and current value is non-zero.")
    assert (s.operation, s.new_removed) == ("new_removed", "new")
    s = parse_operation("Which elements became zero?")
    assert (s.operation, s.new_removed) == ("new_removed", "zero")


def test_trend_and_anomaly_are_flagged():
    assert parse_operation("Show the trend of Assets for the last 12 months.").operation == "trend"
    assert parse_operation("Show month-on-month variation.").granularity == "mom"
    assert parse_operation("Find unusual variations.").operation == "anomaly"
    assert parse_operation("Which values need my attention?").operation == "anomaly"
