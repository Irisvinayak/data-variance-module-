"""Wrong-number fixes (code review H-12..H-16, M-12, M-13, M-15..M-17, L-05..L-07).

Pure logic with stand-ins: no Oracle. Where a fix changes SQL (M-17) or a lookup that
feeds SQL (H-16) the generated text is asserted, but that it runs on a real Oracle is
a separate, pending verification.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import InvalidOperation

import pytest

from backend.data import calculate_variance as cv
from backend.data import report_lookup, service
from backend.nlp import date_intent, date_resolver, return_lookup

# ── H-12 / L-05 / M-16: the number helpers ───────────────────────────────────


def test_a_change_from_zero_is_not_reported_as_zero_percent():
    assert cv.get_pct_change(0, 5)["value"] == "N/A"
    assert cv.get_pct_change("0", "-5")["value"] == "N/A"
    assert cv.get_pct_change(0, 5)["color"] == "success"          # the value did rise
    assert cv.get_pct_change(0, -5)["color"] == "danger"
    assert "N/A" in cv.get_variance_summary(0, 5)["text"]


def test_zero_to_zero_and_ordinary_changes_are_unchanged():
    assert cv.get_pct_change(0, 0) == {"value": "0.00%", "color": ""}
    assert cv.get_pct_change(100, 150) == {"value": "50.00%", "color": "success"}
    assert cv.get_pct_change(200, 100) == {"value": "-50.00%", "color": "danger"}
    assert cv.get_difference(10, 25) == {"value": "15.00", "color": "success"}
    assert cv.get_variance_summary(100, 150)["text"] == "150.00 ▲ +50.00% (Prev: 100.00)"


def test_rounding_is_half_up_not_bankers():
    assert cv.get_difference(0, "0.125")["value"] == "0.13"       # banker's rounding gave 0.12
    assert cv.get_difference(0, "0.135")["value"] == "0.14"
    assert cv.get_pct_change(1000, 1001.25)["value"] == "0.13%"   # 0.125 %


def test_colour_follows_the_rounded_figure_not_the_raw_one():
    d = cv.get_difference("1.000", "1.004")                        # rounds to 0.00
    assert d == {"value": "0.00", "color": ""}
    p = cv.get_pct_change(1000, 1000.04)                           # 0.004 % -> 0.00%
    assert p == {"value": "0.00%", "color": ""}
    s = cv.get_variance_summary(1000, 1000.04)
    assert s["color"] == "" and s["arrow"] == ""


def test_null_policy_is_explicit():
    assert cv.get_difference(None, None) is None
    assert cv.get_difference(None, 5) == {"value": "N/A", "color": ""}    # not "5" shown as the change
    assert cv.get_difference(5, None) == {"value": "N/A", "color": ""}
    assert cv.get_pct_change(None, 5) is None


def test_accounting_negatives_are_parsed():
    assert cv._to_decimal("(100)") == -100
    assert cv._to_decimal("(1,250.50)") == -1250.50
    assert cv.get_difference("(100)", "50")["value"] == "150.00"


@pytest.mark.parametrize("bad", ["NaN", "nan", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_values_are_not_numbers(bad):
    with pytest.raises(InvalidOperation):
        cv._to_decimal(bad)
    assert cv.get_pct_change(bad, 5) is None
    assert cv.get_difference(bad, 5)["value"] == "5"                # text fallback, never "NaN" arithmetic


# ── H-14: matching keys cannot collide ───────────────────────────────────────

def test_distinct_rows_never_share_a_key_even_when_their_display_strings_collide():
    a = {"X": "A_B", "Y": "C"}
    b = {"X": "A", "Y": "B_C"}
    assert cv.build_identifier(a, ["X", "Y"]) == cv.build_identifier(b, ["X", "Y"])     # display collides
    assert cv.build_key(a, ["X", "Y"]) != cv.build_key(b, ["X", "Y"])                   # matching does not


def test_an_empty_part_stays_in_its_column():
    a = {"X": "A", "Y": ""}
    b = {"X": "", "Y": "A"}
    assert cv.build_identifier(a, ["X", "Y"]) == cv.build_identifier(b, ["X", "Y"]) == "A"
    assert cv.build_key(a, ["X", "Y"]) != cv.build_key(b, ["X", "Y"])


def test_keys_ignore_case_of_column_names_and_surrounding_spaces():
    assert cv.build_key({"x": " A ", "y": "B"}, ["X", "Y"]) == ("A", "B")


# ── calculate_variance end to end (stubbed query) ────────────────────────────

def _run(rows, comp=("ID",), comparison_dates=("31-MAR-2025", "31-DEC-2024"), mode="vs_current"):
    meta = {"filter_col": "RDATE", "comp_filter_col_names": list(comp), "is_single": False}
    return cv.calculate_variance(
        return_code="1", table_name="T", reporting_date="31-MAR-2025",
        get_table_metadata_fn=lambda *a, **k: meta,
        execute_query_fn=lambda q, *a: rows,
        comparison_dates=list(comparison_dates), comparison_mode=mode,
    )


CUR, PRV = datetime(2025, 3, 31), datetime(2024, 12, 31)


def test_same_key_twice_in_one_period_is_flagged_not_silent():
    rows = [
        {"RDATE": CUR, "ID": "A", "AMT": 10}, {"RDATE": CUR, "ID": "A", "AMT": 11},
        {"RDATE": PRV, "ID": "A", "AMT": 5},
    ]
    out = _run(rows)
    assert out["duplicate_identifiers"] == ["A"]
    assert "more than once" in out["duplicate_notice"]


def test_duplicates_in_the_previous_period_are_flagged_too():
    rows = [
        {"RDATE": CUR, "ID": "A", "AMT": 10},
        {"RDATE": PRV, "ID": "A", "AMT": 5}, {"RDATE": PRV, "ID": "A", "AMT": 6},
    ]
    assert _run(rows)["duplicate_identifiers"] == ["A"]


def test_unique_keys_add_no_new_response_keys():
    rows = [{"RDATE": CUR, "ID": "A", "AMT": 10}, {"RDATE": PRV, "ID": "A", "AMT": 5}]
    out = _run(rows)
    assert "duplicate_identifiers" not in out and "removed_rows" not in out
    assert out["rows"][0]["previous"]["previous_1"]["AMT"]["change"]["value"] == "5.00"


def test_colliding_display_strings_are_compared_with_their_own_previous_row():
    rows = [
        {"RDATE": CUR, "X": "A_B", "Y": "C", "AMT": 100}, {"RDATE": CUR, "X": "A", "Y": "B_C", "AMT": 200},
        {"RDATE": PRV, "X": "A_B", "Y": "C", "AMT": 90}, {"RDATE": PRV, "X": "A", "Y": "B_C", "AMT": 150},
    ]
    out = _run(rows, comp=("X", "Y"))
    assert "duplicate_identifiers" not in out
    by_amt = {r["current"]["AMT"]: r["previous"]["previous_1"]["AMT"]["value"] for r in out["rows"]}
    assert by_amt == {100: 90, 200: 150}                         # before the fix both matched the same row


def test_a_row_that_disappeared_is_reported():
    rows = [
        {"RDATE": CUR, "ID": "A", "AMT": 10},
        {"RDATE": PRV, "ID": "A", "AMT": 5}, {"RDATE": PRV, "ID": "GONE", "AMT": 99},
    ]
    out = _run(rows)
    assert [r["identifier"] for r in out["removed_rows"]] == ["GONE"]
    assert out["removed_rows"][0]["previous"]["AMT"] == 99
    assert "1 row(s)" in out["removed_notice"]
    assert [r["identifier"] for r in out["rows"]] == ["A"]        # the main table is unchanged


def test_removed_rows_are_capped():
    rows = [{"RDATE": CUR, "ID": "A", "AMT": 1}] + [
        {"RDATE": PRV, "ID": f"G{i}", "AMT": i} for i in range(cv.MAX_REMOVED_ROWS + 25)
    ]
    out = _run(rows)
    assert len(out["removed_rows"]) == cv.MAX_REMOVED_ROWS
    assert f"{cv.MAX_REMOVED_ROWS + 25} row(s)" in out["removed_notice"]


def test_sequential_mode_still_matches_by_key():
    rows = [{"RDATE": CUR, "ID": "A", "AMT": 10}, {"RDATE": PRV, "ID": "A", "AMT": 4}]
    out = _run(rows, mode="sequential")
    assert out["rows"][0]["link_1"]["metrics"]["AMT"]["change"]["value"] == "6.00"


# ── M-17: the date predicate is a day range ──────────────────────────────────

def test_date_predicate_is_a_half_open_day_range():
    sql = cv.build_query("T", {"filter_col": "RDATE"}, datetime(2025, 3, 31), [], "1")
    assert "RDATE >= TO_DATE('31-MAR-2025', 'DD-MON-YYYY')" in sql
    assert "RDATE < TO_DATE('31-MAR-2025', 'DD-MON-YYYY') + 1" in sql
    assert "RDATE = TO_DATE" not in sql


def test_rows_with_a_time_part_still_belong_to_their_day():
    assert cv.dates_match(datetime(2025, 3, 31, 10, 30), datetime(2025, 3, 31))


# ── L-06: ISO timestamps ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["2025-03-31 10:30:00", "2025-03-31T10:30:00", "2025-03-31 10:30:00.123456", "2025-03-31"])
def test_iso_timestamps_match_their_day(text):
    assert cv.dates_match(text, datetime(2025, 3, 31))
    assert not cv.dates_match(text, datetime(2025, 4, 1))


# ── L-07: fortnightly returns snap to their period end ───────────────────────

def test_fortnight_period_boundaries():
    assert date_intent.period_containing(date(2025, 3, 9), "fortnight") == (date(2025, 3, 1), date(2025, 3, 15))
    assert date_intent.period_containing(date(2025, 3, 15), "fortnight")[1] == date(2025, 3, 15)
    assert date_intent.period_containing(date(2025, 3, 16), "fortnight") == (date(2025, 3, 16), date(2025, 3, 31))
    assert date_intent.period_containing(date(2024, 2, 20), "fortnight")[1] == date(2024, 2, 29)


@pytest.mark.parametrize("freq", ["F", "FORTNIGHTLY", "HM"])
def test_fortnightly_frequencies_are_known_to_the_resolver(freq):
    assert date_resolver._FREQ_PERIOD[freq] == ("fortnight", 1)


@pytest.mark.parametrize("freq", ["F", "HM"])
def test_a_non_reporting_day_snaps_to_the_fortnight_end(freq):
    from backend.nlp.date_intent import PeriodRef

    picker = date_resolver._Picker([date(2025, 3, 31), date(2025, 3, 15), date(2025, 2, 28)], freq)
    ref = PeriodRef(start=date(2025, 3, 12), end=date(2025, 3, 12), granularity="day", text="12-Mar-2025")
    assert picker.ref(ref) == date(2025, 3, 15)
    assert any("period end" in n for n in picker.notes)


# ── H-16: the previous-period lookup honours the return filter ───────────────

class Recorder:
    def __init__(self, dates):
        self.dates, self.sql = dates, []

    def __call__(self, sql):
        self.sql.append(sql)
        return ["D"], [(d,) for d in self.dates], None


def test_previous_dates_are_restricted_to_this_returns_rows():
    rec = Recorder([datetime(2024, 12, 31)])
    service._actual_previous_dates("T", "RDATE", "31-MAR-2025", 1, rec,
                                   extra_where="RC = 'R1' AND FREQ = 'Q'")
    assert "AND RC = 'R1' AND FREQ = 'Q'" in rec.sql[0]
    assert "RDATE < TO_DATE('31-MAR-2025', 'DD-MON-YYYY')" in rec.sql[0]


def test_previous_dates_skip_dates_that_are_not_canonical_for_the_frequency():
    # A quarterly return sharing a table with a monthly one: only quarter ends count.
    rec = Recorder([datetime(2025, 2, 28), datetime(2025, 1, 31), datetime(2024, 12, 31), datetime(2024, 9, 30)])
    out = service._actual_previous_dates("T", "RDATE", "31-MAR-2025", 2, rec, report_freq="Q")
    assert out == ["31-DEC-2024", "30-SEP-2024"]


def test_previous_dates_without_a_frequency_are_unchanged():
    rec = Recorder([datetime(2025, 2, 28), datetime(2025, 1, 31)])
    assert service._actual_previous_dates("T", "RDATE", "31-MAR-2025", 2, rec) == ["28-FEB-2025", "31-JAN-2025"]
    assert "FETCH FIRST 2 ROWS" in rec.sql[0]


def test_previous_dates_fall_back_to_the_unfiltered_list_when_none_is_canonical():
    rec = Recorder([datetime(2025, 2, 27), datetime(2025, 1, 15)])
    assert service._actual_previous_dates("T", "RDATE", "31-MAR-2025", 2, rec, report_freq="Q") == ["27-FEB-2025", "15-JAN-2025"]


def test_return_filter_sql_mirrors_build_query():
    meta = {"is_single": True, "return_code_col": "RC", "return_code": "O'Brien", "freq_col": "FREQ", "freq_val": "Q"}
    assert service._return_filter_sql(meta) == "RC = 'O''Brien' AND FREQ = 'Q'"
    assert service._return_filter_sql({"is_single": False, "return_code_col": "RC", "return_code": "1"}) == ""
    with pytest.raises(ValueError):
        service._return_filter_sql({**meta, "return_code_col": "RC; DROP"})
    with pytest.raises(ValueError):
        service._return_filter_sql({**meta, "freq_col": "F X"})


# ── M-12 / M-13: Excel (non-XBRL) returns are known ──────────────────────────

def test_parse_all_returns_adds_non_xbrl_returns_and_xbrl_wins_a_clash(monkeypatch):
    monkeypatch.setattr(report_lookup, "parse_returns", lambda ctx=None: ({"Id": "1", "Name": "X"}, {"Id": "2", "Name": "Y"}))
    monkeypatch.setattr(report_lookup, "_parse_non_xbrl_returns",
                        lambda ctx=None: ({"Id": "2", "Name": "CLASH"}, {"Id": "9", "Name": "Excel", "IsExcel": "true"}))
    out = report_lookup.parse_all_returns()
    assert [r["Id"] for r in out] == ["1", "2", "9"]
    assert next(r for r in out if r["Id"] == "2")["Name"] == "Y"


def test_parse_all_returns_survives_a_missing_non_xbrl_master(monkeypatch):
    def boom(ctx=None):
        raise FileNotFoundError("NonXBRLReturns.xml")

    monkeypatch.setattr(report_lookup, "parse_returns", lambda ctx=None: ({"Id": "1"},))
    monkeypatch.setattr(report_lookup, "_parse_non_xbrl_returns", boom)
    assert report_lookup.parse_all_returns() == ({"Id": "1"},)


def test_an_excel_return_is_found_by_the_service_and_is_not_given_a_dp_suffix(monkeypatch):
    monkeypatch.setattr(service, "parse_all_returns", lambda ctx: ({"Id": "9", "IsExcel": "true", "TblPath": "t.xml"},))
    meta = service._return_meta("9", None)
    assert meta["IsExcel"] == "true"
    monkeypatch.setattr(service, "IS_SP_TABLE_DATA_ENABLED", True)
    assert service.resolve_physical_table_name("9", "MY_TABLE", return_meta=meta) == "MY_TABLE"


def test_the_nlp_lookup_registers_tables_of_excel_returns(monkeypatch):
    monkeypatch.setattr(return_lookup, "parse_all_returns",
                        lambda ctx: ({"Id": "9", "Name": "Excel Return", "RepFreq": "M", "TblPath": "t.xml"},))

    class Row:
        attrib = {"TableName": "EXCEL_T", "FilterColumn": "RDATE", "CompFilterColName": "ID"}

    class Root:
        def findall(self, tag):
            return [Row()] if tag == "Row" else []

    monkeypatch.setattr(return_lookup, "_load_table_mapping", lambda rid, path, ctx: (Root(), "t.xml"))
    monkeypatch.setattr(return_lookup.query_xml_lookup, "query_xml_label", lambda ctx: "x", raising=False)
    lookup = return_lookup._build_lookup(return_lookup.ANONYMOUS)
    assert lookup["EXCEL_T"][0]["return_id"] == "9"
