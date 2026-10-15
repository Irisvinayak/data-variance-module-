"""M-14 (fiscal period-end months follow the host) and L-09 (comparison-date ceiling, pinned).

Yearly-return data here is representative MOCK data (period-end dates only); no real QCB
return was used. Whether a real 6.0 yearly return uses a December period end is the manual
verification step recorded in docs/ISSUES_STATUS.md.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from backend.auth.deps import require_login
from backend.config import RequestContext
from backend.data import calculate_variance as cv
from backend.main import app
from backend.nlp import date_resolver

D = datetime


def _with_start(monkeypatch, month):
    monkeypatch.setattr(cv, "_fiscal_year_start_month", lambda: month)


# ── the month arithmetic ─────────────────────────────────────────────────────

@pytest.mark.parametrize("start,annual,half", [
    (4, 3, {3, 9}),          # 5.5: Apr-Mar, exactly the old hard-coded values
    (1, 12, {6, 12}),        # 6.0: calendar fiscal year
    (7, 6, {6, 12}),         # a July-June year
    (10, 9, {9, 3}),         # an Oct-Sep year
])
def test_period_end_months_follow_the_fiscal_year_start(start, annual, half):
    assert cv.fiscal_period_end_months(start) == (annual, frozenset(half))


# ── 5.5 is unchanged ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("freq", ["A", "Y", "FY", "ANNUAL"])
def test_5_5_annual_still_means_31_march_only(monkeypatch, freq):
    _with_start(monkeypatch, 4)
    assert cv.validate_reporting_date(D(2025, 3, 31), freq)
    for bad in (D(2025, 12, 31), D(2025, 6, 30), D(2025, 3, 30)):
        assert not cv.validate_reporting_date(bad, freq)


@pytest.mark.parametrize("freq", ["H", "HY", "FH", "HALFYEARLY"])
def test_5_5_half_yearly_still_means_march_and_september(monkeypatch, freq):
    _with_start(monkeypatch, 4)
    assert cv.validate_reporting_date(D(2025, 3, 31), freq) and cv.validate_reporting_date(D(2024, 9, 30), freq)
    assert not cv.validate_reporting_date(D(2024, 12, 31), freq) and not cv.validate_reporting_date(D(2025, 6, 30), freq)


# ── 6.0: a yearly return filed on 31 December is valid ───────────────────────

@pytest.mark.parametrize("freq", ["A", "Y", "FY"])
def test_6_0_annual_accepts_31_december_and_rejects_31_march(monkeypatch, freq):
    _with_start(monkeypatch, 1)
    assert cv.validate_reporting_date(D(2024, 12, 31), freq)
    assert not cv.validate_reporting_date(D(2025, 3, 31), freq)          # was the only accepted date before


@pytest.mark.parametrize("freq", ["H", "HY", "FH"])
def test_6_0_half_yearly_accepts_june_and_december(monkeypatch, freq):
    _with_start(monkeypatch, 1)
    assert cv.validate_reporting_date(D(2024, 6, 30), freq) and cv.validate_reporting_date(D(2024, 12, 31), freq)
    assert not cv.validate_reporting_date(D(2024, 9, 30), freq)


def test_calendar_year_codes_do_not_depend_on_the_host(monkeypatch):
    for start in (1, 4):
        _with_start(monkeypatch, start)
        assert cv.validate_reporting_date(D(2024, 12, 31), "B") and not cv.validate_reporting_date(D(2025, 3, 31), "B")
        assert cv.validate_reporting_date(D(2024, 6, 30), "C") and cv.validate_reporting_date(D(2024, 12, 31), "CH")


def test_other_frequencies_and_the_day_rule_are_untouched(monkeypatch):
    _with_start(monkeypatch, 1)
    assert cv.validate_reporting_date(D(2025, 2, 28), "M") and cv.validate_reporting_date(D(2025, 6, 30), "Q")
    assert not cv.validate_reporting_date(D(2024, 12, 30), "A")           # must still be the last day
    assert cv.validate_reporting_date(D(2025, 1, 3), "D")


def test_the_host_default_is_unchanged_for_the_running_profile():
    from backend.hosts import get_profile

    assert cv._fiscal_year_start_month() == get_profile().fiscal_year_start_month


def test_a_broken_profile_falls_back_to_the_old_months(monkeypatch):
    def boom():
        raise RuntimeError("no profile")

    monkeypatch.setattr("backend.hosts.get_profile", boom)
    assert cv._fiscal_year_start_month() == 4
    assert cv.fiscal_period_end_months() == (3, frozenset({3, 9}))


# ── representative yearly-return mock data, end to end ───────────────────────

def _yearly_rows(period_ends):
    return [{"RDATE": d, "ID": "A", "AMT": 100 + i} for i, d in enumerate(period_ends)]


def _compute_yearly(rows, reporting_date):
    meta = {"filter_col": "RDATE", "comp_filter_col_names": ["ID"], "is_single": False, "report_freq": "A"}
    return cv.calculate_variance(
        return_code="Y1", table_name="T", reporting_date=reporting_date,
        get_table_metadata_fn=lambda *a, **k: meta, execute_query_fn=lambda q, *a: rows,
        reporting_period=1,
    )


def test_a_6_0_yearly_return_on_31_december_is_computed_not_rejected(monkeypatch):
    _with_start(monkeypatch, 1)
    out = _compute_yearly(_yearly_rows([D(2024, 12, 31), D(2023, 12, 31)]), "31-DEC-2024")
    assert "error" not in out
    assert out["comparison_periods"] == ["31-DEC-2023"]
    assert out["rows"][0]["previous"]["previous_1"]["AMT"]["change"]["value"] == "-1.00"


def test_the_same_request_on_the_5_5_rules_is_still_rejected(monkeypatch):
    _with_start(monkeypatch, 4)
    out = _compute_yearly(_yearly_rows([D(2024, 12, 31), D(2023, 12, 31)]), "31-DEC-2024")
    assert out == {"error": "Invalid Reporting Date According To Frequency."}


# ── the NL date resolver snaps to the same period end ────────────────────────

def test_the_resolver_snaps_a_yearly_return_to_the_hosts_year_end(monkeypatch):
    from backend.nlp.date_intent import PeriodRef

    ref = PeriodRef(start=date(2024, 12, 20), end=date(2024, 12, 20), granularity="day", text="20-Dec-2024")
    cands = [date(2024, 12, 31), date(2023, 12, 31)]
    _with_start(monkeypatch, 1)
    assert date_resolver._Picker(cands, "A").ref(ref) == date(2024, 12, 31)
    _with_start(monkeypatch, 4)
    cands5 = [date(2025, 3, 31), date(2024, 3, 31)]
    ref5 = PeriodRef(start=date(2025, 3, 20), end=date(2025, 3, 20), granularity="day", text="20-Mar-2025")
    assert date_resolver._Picker(cands5, "A").ref(ref5) == date(2025, 3, 31)


# ── L-09: the ceiling is pinned (the intent needs a product decision) ────────

@pytest.fixture
def client():
    app.dependency_overrides[require_login] = lambda: RequestContext("tester", "")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(require_login, None)


BODY = {"return_id": "2041", "table_mapping_path": "x.xml", "table_name": "T",
        "reporting_date": "31-MAR-2025", "reporting_period": 1, "comparison_mode": "vs_current"}


def test_the_explicit_date_ceiling_counts_the_current_date(client, monkeypatch):
    """CURRENT behaviour, deliberately pinned: 3 dates INCLUDING the current one (so 2 previous),
    while the derived Periods chips allow 3 previous. Whether that difference is intended is
    the open product decision (L-09); changing it needs the UI limit (ControlBar MAX_DATES) too."""
    from backend.data import service

    monkeypatch.setattr(service, "compute_variance", lambda **kw: {"rows": [], "columns": []})
    three = ["31-MAR-2025", "31-DEC-2024", "30-SEP-2024"]
    assert client.post("/variance/compute", json={**BODY, "comparison_dates": three}).status_code == 200
    four = three + ["30-JUN-2024"]
    r = client.post("/variance/compute", json={**BODY, "comparison_dates": four})
    assert r.status_code == 400 and "At most 3" in r.text


def test_duplicates_do_not_count_towards_the_ceiling(client, monkeypatch):
    from backend.data import service

    monkeypatch.setattr(service, "compute_variance", lambda **kw: {"rows": [], "columns": []})
    dates = ["31-MAR-2025", "31-MAR-2025", "31-DEC-2024", "30-SEP-2024"]
    assert client.post("/variance/compute", json={**BODY, "comparison_dates": dates}).status_code == 200


def test_the_derived_period_ceiling_is_separate_and_larger(client, monkeypatch):
    from backend.api import variance
    from backend.data import service

    assert variance.MAX_REPORTING_PERIOD == 12 > variance.MAX_COMPARISON_DATES
    monkeypatch.setattr(service, "compute_variance", lambda **kw: {"rows": [], "columns": []})
    assert client.post("/variance/compute", json={**BODY, "reporting_period": 3}).status_code == 200


def test_the_nl_resolver_uses_the_same_ceiling():
    assert date_resolver._MAX_COMPARISON_DATES == 3 and date_resolver._MAX_OLDER_DATES == 2
