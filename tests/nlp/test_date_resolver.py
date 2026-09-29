"""date_resolver maps a query's date intent onto dates the table really has.

The DB is replaced by fixture date lists shaped like the real tables:
monthly, quarterly, QCB F010 (declares daily, files monthly), truly daily and
gappy. Every scenario must produce an anchor plus comparison dates — the
"every query executes" rule — and any substitution must come with a note.
"""
from __future__ import annotations

import calendar
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backend.hosts as hosts  # noqa: E402
from backend.nlp import date_resolver  # noqa: E402


def _month_ends(y0, m0, n):
    out, y, m = [], y0, m0
    for _ in range(n):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


MONTHLY = _month_ends(2025, 6, 30)                                   # Jun-2025 back 30 months
QUARTERLY = [d for d in MONTHLY if d.month in (3, 6, 9, 12)]
DAILY = [date(2025, 6, 30) - timedelta(days=i) for i in range(120)]
SPARSE_Q = [date(2025, 3, 31), date(2024, 9, 30), date(2024, 3, 31), date(2023, 3, 31)]


@pytest.fixture
def run(monkeypatch):
    def _run(query, cands, freq="Q", fy=4):
        monkeypatch.setattr(date_resolver, "_candidate_dates", lambda *a, **k: sorted(cands, reverse=True))
        monkeypatch.setattr(hosts, "get_profile", lambda *a, **k: SimpleNamespace(fiscal_year_start_month=fy))
        anchor, periods, comparison, notes = date_resolver.resolve_reporting_date(
            query, "2041", "T", "RDATE", freq,
        )
        assert comparison and comparison[0] == anchor          # always executable
        assert len(comparison) <= 3                            # manual-route limit
        return anchor, comparison[1:], notes
    return _run


# ── quarterly table ───────────────────────────────────────────────────────────

def test_no_date_is_latest_vs_previous(run):
    assert run("total loans", QUARTERLY)[:2] == ("30-JUN-2025", ["31-MAR-2025"])


def test_two_explicit_dates_keep_the_named_date(run):
    a, comps, _ = run("31-Mar-2025 vs 31-Mar-2024", QUARTERLY)
    assert (a, comps) == ("31-MAR-2025", ["31-MAR-2024"])


def test_yoy_is_one_year_back_not_two_quarters(run):
    assert run("YoY", QUARTERLY)[1] == ["30-JUN-2024"]
    assert run("same quarter last year", QUARTERLY)[1] == ["30-JUN-2024"]


def test_month_resolves_to_its_own_end(run):
    assert run("March 2025", QUARTERLY)[0] == "31-MAR-2025"


def test_non_period_end_day_snaps_to_period_end(run):
    a, _c, notes = run("on 30-Mar-2025", QUARTERLY)
    assert a == "31-MAR-2025" and notes


def test_fiscal_quarter_per_host(run):
    assert run("Q1FY25", QUARTERLY, fy=4)[0] == "30-JUN-2024"     # 5.5: Apr-Jun 2024
    assert run("Q1FY25", QUARTERLY, fy=1)[0] == "31-MAR-2025"     # 6.0: Jan-Mar 2025


def test_calendar_quarter_and_year(run):
    assert run("Q1 2025", QUARTERLY)[0] == "31-MAR-2025"
    assert run("2024", QUARTERLY)[0] == "31-DEC-2024"
    assert run("FY25", QUARTERLY, fy=4)[0] == "31-MAR-2025"


def test_explicit_anchor_survives_relative(run):
    a, comps, _ = run("as of 31-Mar-2025 vs last 2 quarters", QUARTERLY)
    assert (a, comps) == ("31-MAR-2025", ["31-DEC-2024", "30-SEP-2024"])


def test_explicit_anchor_with_qoq(run):
    assert run("Q1FY25 QoQ", QUARTERLY)[:2] == ("30-JUN-2024", ["31-MAR-2024"])


def test_cap_keeps_nearest_and_furthest_with_note(run):
    a, comps, notes = run("last 4 quarters", QUARTERLY)
    assert comps == ["31-MAR-2025", "30-JUN-2024"]
    assert any("limit" in n for n in notes)


def test_since_compares_back_to_start(run):
    a, comps, _ = run("since Q1FY25", QUARTERLY)        # Apr-2024 onwards
    assert a == "30-JUN-2025" and comps[-1] == "30-JUN-2024"


def test_range(run):
    a, comps, _ = run("between Q1 2024 and Q4 2024", QUARTERLY)
    assert a == "31-DEC-2024" and comps[-1] == "31-MAR-2024"


# ── unit differs from table frequency (calendar-aware) ────────────────────────

def test_quarters_on_monthly_table(run):
    assert run("last 2 quarters", MONTHLY, "M")[1] == ["31-MAR-2025", "31-DEC-2024"]


def test_last_year_on_monthly_table(run):
    assert run("last year", MONTHLY, "M")[1] == ["30-JUN-2024"]


def test_periods_mean_submissions(run):
    assert run("last 2 periods", MONTHLY, "M")[1] == ["31-MAY-2025", "30-APR-2025"]


def test_qcb_f010_declared_daily_files_monthly(run):
    assert run("last 2 months", MONTHLY, "D", fy=1)[1] == ["31-MAY-2025", "30-APR-2025"]


def test_truly_daily_last_month(run):
    assert run("last month", DAILY, "D", fy=1)[1] == ["31-MAY-2025"]


# ── edges: never dead-end ─────────────────────────────────────────────────────

def test_future_date_uses_latest_with_note(run):
    a, _c, notes = run("31-Mar-2027", QUARTERLY)
    assert a == "30-JUN-2025" and notes


def test_before_earliest_uses_earliest_with_note(run):
    a, comps, notes = run("March 2015", QUARTERLY)
    assert a == QUARTERLY[-1].strftime("%d-%b-%Y").upper() and comps == [] and notes


def test_gap_uses_nearest_earlier_with_note(run):
    a, _c, notes = run("Q4 2024", SPARSE_Q)
    assert a == "30-SEP-2024" and notes


def test_yoy_with_gap_notes_substitution(run):
    a, comps, notes = run("30-Sep-2024 YoY", SPARSE_Q)
    # 30-Sep-2023 is missing; the nearest on or before it is 31-Mar-2023.
    assert comps == ["31-MAR-2023"] and notes


def test_single_date_table_still_executes(run):
    a, comps, _ = run("last 3 quarters", [date(2025, 3, 31)])
    assert (a, comps) == ("31-MAR-2025", [])


def test_empty_table_raises(monkeypatch):
    monkeypatch.setattr(date_resolver, "_candidate_dates", lambda *a, **k: [])
    with pytest.raises(ValueError):
        date_resolver.resolve_reporting_date("x", "1", "T", "RDATE", "Q")
