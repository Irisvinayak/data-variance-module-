"""date_intent reads every date phrase a user can type into the NLP bar.

One table row per scenario, run under both fiscal-year conventions: 5.5 (RBI)
uses Apr-Mar, 6.0 (QCB) the calendar year. Each row states the anchor period,
any explicit comparison periods, and the relative / XoX / since / range parts.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.nlp.date_intent import parse_date_intent, step_back  # noqa: E402

TODAY = date(2025, 6, 30)   # newest data date the resolver passes in
D = date


def _span(ref):
    return None if ref is None else (ref.start, ref.end, ref.granularity)


# (query, fy_start, anchor, targets, relative, xox)
SCENARIOS = [
    # nothing / latest
    ("total loan assets", 4, None, [], None, None),
    ("latest total loan assets", 4, None, [], None, None),
    ("2 exposures by sector", 4, None, [], None, None),      # a bare number is not a date
    ("market value 2025", 4, (D(2025, 1, 1), D(2025, 12, 31), "year"), [], None, None),
    # exact days, day-first
    ("as of 31-Mar-2025", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], None, None),
    ("31/03/2025", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], None, None),
    ("01/03/2025", 4, (D(2025, 3, 1), D(2025, 3, 1), "day"), [], None, None),
    ("2025-03-31", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], None, None),
    ("31.03.2025", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], None, None),
    ("March 31, 2025", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], None, None),
    ("31st March 2025", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], None, None),
    # months — a whole month, not its 1st
    ("March 2025", 4, (D(2025, 3, 1), D(2025, 3, 31), "month"), [], None, None),
    ("Mar-25", 4, (D(2025, 3, 1), D(2025, 3, 31), "month"), [], None, None),
    ("in Jan 2025", 4, (D(2025, 1, 1), D(2025, 1, 31), "month"), [], None, None),
    # calendar quarters / halves — independent of the fiscal year
    ("Q1 2025", 4, (D(2025, 1, 1), D(2025, 3, 31), "quarter"), [], None, None),
    ("2025 Q1", 1, (D(2025, 1, 1), D(2025, 3, 31), "quarter"), [], None, None),
    ("H1 2025", 4, (D(2025, 1, 1), D(2025, 6, 30), "half"), [], None, None),
    # fiscal quarters / halves / years — depend on the host
    ("Q1FY25", 4, (D(2024, 4, 1), D(2024, 6, 30), "quarter"), [], None, None),
    ("Q1FY25", 1, (D(2025, 1, 1), D(2025, 3, 31), "quarter"), [], None, None),
    ("Q1 of FY25", 4, (D(2024, 4, 1), D(2024, 6, 30), "quarter"), [], None, None),
    ("FY25 Q1", 4, (D(2024, 4, 1), D(2024, 6, 30), "quarter"), [], None, None),
    ("Q4 FY2024-25", 4, (D(2025, 1, 1), D(2025, 3, 31), "quarter"), [], None, None),
    ("H2 FY25", 4, (D(2024, 10, 1), D(2025, 3, 31), "half"), [], None, None),
    ("FY25", 4, (D(2024, 4, 1), D(2025, 3, 31), "year"), [], None, None),
    ("FY25", 1, (D(2025, 1, 1), D(2025, 12, 31), "year"), [], None, None),
    ("FY2024-25", 4, (D(2024, 4, 1), D(2025, 3, 31), "year"), [], None, None),
    ("financial year 2025", 4, (D(2024, 4, 1), D(2025, 3, 31), "year"), [], None, None),
    ("CY2024", 4, (D(2024, 1, 1), D(2024, 12, 31), "year"), [], None, None),
    ("2024", 4, (D(2024, 1, 1), D(2024, 12, 31), "year"), [], None, None),
    ("Q3", 4, (D(2024, 10, 1), D(2024, 12, 31), "quarter"), [], None, None),  # most recent Q3
    # relative
    ("last quarter", 4, None, [], (1, "quarter"), None),
    ("previous month", 4, None, [], (1, "month"), None),
    ("last year", 4, None, [], (1, "year"), None),
    ("last 2 quarters", 4, None, [], (2, "quarter"), None),
    ("last 2 quaters", 4, None, [], (2, "quarter"), None),     # typo normalised
    ("trailing four quarters", 4, None, [], (4, "quarter"), None),
    ("last 5 periods", 4, None, [], (5, "period"), None),
    ("3 quarters ago", 4, None, [], (3, "quarter"), None),
    # XoX
    ("QoQ", 4, None, [], None, "quarter"),
    ("MoM change", 4, None, [], None, "month"),
    ("YoY", 4, None, [], None, "year"),
    ("year over year", 4, None, [], None, "year"),
    # comparisons
    ("Mar 2025 vs Mar 2024", 4, (D(2025, 3, 1), D(2025, 3, 31), "month"),
     [(D(2024, 3, 1), D(2024, 3, 31), "month")], None, None),
    ("31-Mar-2025 vs 31-Dec-2024 vs 30-Sep-2024", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"),
     [(D(2024, 12, 31), D(2024, 12, 31), "day"), (D(2024, 9, 30), D(2024, 9, 30), "day")], None, None),
    # mixed: explicit anchor + relative / XoX
    ("as of 31-Mar-2025 vs last 2 quarters", 4, (D(2025, 3, 31), D(2025, 3, 31), "day"), [], (2, "quarter"), None),
    ("Q1FY25 QoQ", 4, (D(2024, 4, 1), D(2024, 6, 30), "quarter"), [], None, "quarter"),
    ("March 2025 YoY", 4, (D(2025, 3, 1), D(2025, 3, 31), "month"), [], None, "year"),
]


@pytest.mark.parametrize("query,fy,anchor,targets,relative,xox", SCENARIOS)
def test_scenario(query, fy, anchor, targets, relative, xox):
    intent = parse_date_intent(query, fy, TODAY)
    assert _span(intent.anchor) == anchor
    assert [_span(t) for t in intent.targets] == targets
    assert intent.relative == relative
    assert intent.xox == xox


def test_since():
    intent = parse_date_intent("since Q1FY24", 4, TODAY)
    assert intent.since.start == D(2023, 4, 1)
    assert intent.anchor is None


def test_from_to_is_a_range_not_since():
    intent = parse_date_intent("from Jan 2025 to Jun 2025", 4, TODAY)
    assert intent.since is None
    assert (intent.range[0].start, intent.range[1].end) == (D(2025, 1, 1), D(2025, 6, 30))


def test_between_and_is_a_range():
    intent = parse_date_intent("between Jan 2025 and Mar 2025", 4, TODAY)
    assert (intent.range[0].start, intent.range[1].end) == (D(2025, 1, 1), D(2025, 3, 31))


def test_step_back_keeps_month_ends():
    assert step_back(D(2025, 3, 31), "quarter", 1) == D(2024, 12, 31)
    assert step_back(D(2025, 2, 28), "year", 1) == D(2024, 2, 29)
    assert step_back(D(2025, 3, 31), "month", 1) == D(2025, 2, 28)
