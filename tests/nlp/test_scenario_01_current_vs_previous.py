"""Scenario 1 — basic current vs previous comparison (English prompts only).

Each prompt from the requirement runs through the real pipeline stages that are DB-free:
date_resolver (which periods?) -> parse_operation (what to do?) -> analyze (summary).
The DB is replaced by fixture date lists, as in test_date_resolver.py.
"""
from __future__ import annotations

import calendar
import os
import sys
from datetime import date
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backend.hosts as hosts  # noqa: E402
from backend.nlp import date_resolver  # noqa: E402
from backend.nlp.analysis import analyze  # noqa: E402
from backend.nlp.operation_parser import parse_operation  # noqa: E402


def _month_ends(y0, m0, n):
    out, y, m = [], y0, m0
    for _ in range(n):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


MONTHLY = _month_ends(2025, 6, 30)
QUARTERLY = [d for d in MONTHLY if d.month in (3, 6, 9, 12)]
YEARLY = [d for d in MONTHLY if d.month == 12]


@pytest.fixture
def run(monkeypatch):
    def _run(query, cands, freq):
        monkeypatch.setattr(date_resolver, "_candidate_dates", lambda *a, **k: sorted(cands, reverse=True))
        monkeypatch.setattr(hosts, "get_profile", lambda *a, **k: SimpleNamespace(fiscal_year_start_month=1))
        anchor, _p, comparison, _n = date_resolver.resolve_reporting_date(query, "2041", "T", "RDATE", freq)
        return anchor, comparison[1:]
    return _run


# ── which periods ─────────────────────────────────────────────────────────────
LATEST_VS_PREVIOUS_MONTHLY = [
    "Compare the current data with the previous period.",
    "Show me the variation between current and previous data.",
    "Perform data variation analysis for the latest two periods.",
    "Compare this month’s data with last month’s data.",
    "What has changed between the current and previous reporting period?",
    "Show all changes between the two periods.",
    "Give me a summary of the changes.",
]


@pytest.mark.parametrize("prompt", LATEST_VS_PREVIOUS_MONTHLY)
def test_defaults_to_latest_vs_one_back(run, prompt):
    assert run(prompt, MONTHLY, "M") == ("30-JUN-2025", ["31-MAY-2025"])


def test_current_quarter_vs_previous_quarter(run):
    assert run("Compare current quarter data with previous quarter.", QUARTERLY, "Q") == (
        "30-JUN-2025", ["31-MAR-2025"])


def test_current_quarter_on_monthly_table_steps_a_quarter(run):
    assert run("Compare current quarter data with previous quarter.", MONTHLY, "M") == (
        "30-JUN-2025", ["31-MAR-2025"])


def test_current_year_vs_previous_year(run):
    assert run("Compare current year data with previous year.", MONTHLY, "M") == (
        "30-JUN-2025", ["30-JUN-2024"])


# ── what to do: nothing beyond the plain table ────────────────────────────────
@pytest.mark.parametrize("prompt", LATEST_VS_PREVIOUS_MONTHLY + [
    "Compare current quarter data with previous quarter.",
    "Compare current year data with previous year.",
])
def test_plain_comparison_is_not_filtered(prompt):
    spec = parse_operation(prompt)
    assert spec.operation in ("compare", "filter")
    assert spec.threshold is None and spec.top_n is None and spec.bottom_n is None
    assert spec.group_by == "element"


# ── the summary the user reads ────────────────────────────────────────────────
def _result():
    def row(label, prev, curr):
        return {"display_label": label, "current": {"AMT": curr},
                "previous": {"previous_1": {"AMT": {"value": prev}}}}
    return {"columns": ["AMT"], "rows": [
        row("A", "100", "150"),    # increased
        row("B", "100", "50"),     # decreased
        row("C", "100", "100"),    # unchanged
        row("D", "0", "10"),       # new
        row("E", "10", "0"),       # became zero
    ]}


def test_summary_counts_changes():
    out = analyze(_result(), parse_operation("Give me a summary of the changes."))
    assert out["analysis"]["counts"] == {
        "increased": 1, "decreased": 1, "unchanged": 1, "new": 1, "became_zero": 1}
    assert "Compared 5 rows" in out["summary"]
    assert len(out["rows"]) == 5          # plain comparison keeps every row


def test_what_has_changed_drops_unchanged_rows():
    out = analyze(_result(), parse_operation("What has changed between the current and previous reporting period?"))
    labels = [r["display_label"] for r in out["rows"]]
    assert labels == ["A", "B", "D", "E"]
