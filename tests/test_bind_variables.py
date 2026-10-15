"""M-06: values can travel as Oracle bind variables (DV_USE_BIND_VARIABLES).

Everything here uses stand-ins: the SQL text, the bind dictionaries and the driver call are
checked, NOT Oracle's behaviour. That a real Oracle accepts these statements and returns the
same rows as the literal form is the manual verification recorded in docs/ISSUES_STATUS.md.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from backend.config import settings as cfg
from backend.data import calculate_variance as cv
from backend.data import db, service

META = {"filter_col": "RDATE", "is_single": True, "return_code_col": "RC",
        "freq_col": "FREQ", "freq_val": "Q"}
DATES = (datetime(2025, 3, 31), [datetime(2024, 12, 31), datetime(2024, 9, 30)])


def literal(**kw):
    return cv.build_query("SCH.T", kw.get("meta", META), DATES[0], DATES[1], kw.get("rc", "RC1"))


def bound(**kw):
    binds: dict = {}
    sql = cv.build_query("SCH.T", kw.get("meta", META), DATES[0], DATES[1], kw.get("rc", "RC1"), binds=binds)
    return sql, binds


def inline(sql: str, binds: dict) -> str:
    """Substitute the binds back as literals - the inverse of what the driver does."""
    def put(m):
        name = m.group(1)
        value = binds[name]
        return f"'{value}'" if name.startswith("d") else cv.sql_literal(value)

    return re.sub(r":([a-z]\w*)", put, sql)


def test_the_default_is_off_and_the_literal_text_is_unchanged():
    assert cfg.USE_BIND_VARIABLES is False
    sql = literal()
    assert "TO_DATE('31-MAR-2025'" in sql and "RC = 'RC1'" in sql and ":" not in sql


# ── the bound statement ──────────────────────────────────────────────────────

def test_dates_and_values_become_named_placeholders():
    sql, binds = bound()
    assert binds == {"d0": "31-MAR-2025", "d1": "31-DEC-2024", "d2": "30-SEP-2024", "rc": "RC1", "fv": "Q"}
    for name in binds:
        assert f":{name}" in sql
    assert "31-MAR-2025" not in sql and "'RC1'" not in sql and "'Q'" not in sql


def test_the_bound_statement_is_the_literal_statement_with_the_values_moved_out():
    sql, binds = bound()
    assert inline(sql, binds) == literal()


def test_a_date_is_bound_once_and_used_for_both_ends_of_its_day_range():
    sql, binds = bound()
    assert sql.count(":d0") == 2 and len([k for k in binds if k.startswith("d")]) == 3


def test_a_hostile_return_code_is_only_ever_in_the_binds():
    hostile = "RC1' OR '1'='1"
    sql, binds = bound(rc=hostile)
    assert binds["rc"] == hostile
    assert "OR '1'='1" not in sql and ":rc" in sql


def test_optional_parts_only_bind_what_they_use():
    sql, binds = bound(meta={"filter_col": "RDATE", "is_single": False})
    assert set(binds) == {"d0", "d1", "d2"} and ":rc" not in sql and ":fv" not in sql


@pytest.mark.parametrize("field,value", [("filter_col", "RDATE) OR 1=1 --"), ("return_code_col", "RC = 1 OR 1"),
                                         ("freq_col", "FREQ; DROP TABLE X")])
def test_identifiers_are_still_validated_when_values_are_bound(field, value):
    with pytest.raises(ValueError, match="Unsafe"):
        cv.build_query("T", {**META, field: value}, DATES[0], [], "RC1", binds={})


def test_an_unsafe_table_name_is_still_refused_when_values_are_bound():
    with pytest.raises(ValueError, match="Unsafe table"):
        cv.build_query("T WHERE 1=1 --", META, DATES[0], [], "RC1", binds={})


def test_selected_columns_are_still_checked_when_values_are_bound():
    with pytest.raises(ValueError, match="Invalid column"):
        cv.build_query("T", META, DATES[0], [], "RC1", ["A", "B; DROP"], binds={})


# ── calculate_variance end to end ────────────────────────────────────────────

ROWS = [
    {"RDATE": datetime(2025, 3, 31), "ID": "A", "AMT": 110},
    {"RDATE": datetime(2024, 12, 31), "ID": "A", "AMT": 100},
]


def _calc(monkeypatch, flag, calls):
    monkeypatch.setattr(cfg, "USE_BIND_VARIABLES", flag)

    def execute(query, *args):
        calls.append((query, args))
        return ROWS

    meta = {"filter_col": "RDATE", "comp_filter_col_names": ["ID"], "is_single": True,
            "return_code_col": "RC", "freq_col": None, "freq_val": None}
    return cv.calculate_variance(
        return_code="RC1", table_name="T", reporting_date="31-MAR-2025",
        get_table_metadata_fn=lambda *a, **k: meta, execute_query_fn=execute,
        comparison_dates=["31-MAR-2025", "31-DEC-2024"],
    )


def test_with_the_flag_off_the_executor_gets_only_the_text(monkeypatch):
    calls = []
    _calc(monkeypatch, False, calls)
    assert len(calls) == 1 and calls[0][1] == () and "RC = 'RC1'" in calls[0][0]


def test_with_the_flag_on_the_executor_gets_the_binds_and_the_result_is_identical(monkeypatch):
    off_calls, on_calls = [], []
    off = _calc(monkeypatch, False, off_calls)
    on = _calc(monkeypatch, True, on_calls)
    query, args = on_calls[0]
    assert args == (None, {"d0": "31-MAR-2025", "d1": "31-DEC-2024", "rc": "RC1"})
    assert ":rc" in query and "'RC1'" not in query
    assert on == off                                        # the computation does not depend on the transport
    assert inline(query, args[1]) == off_calls[0][0]


def test_a_connection_string_is_still_passed_through_with_binds(monkeypatch):
    monkeypatch.setattr(cfg, "USE_BIND_VARIABLES", True)
    seen = []
    meta = {"filter_col": "RDATE", "comp_filter_col_names": ["ID"], "is_single": False}
    cv.calculate_variance(
        return_code="1", table_name="T", reporting_date="31-MAR-2025",
        get_table_metadata_fn=lambda *a, **k: meta, execute_query_fn=lambda *a: seen.append(a) or ROWS,
        connection_string="dsn", comparison_dates=["31-MAR-2025"],
    )
    assert seen[0][1] == "dsn" and isinstance(seen[0][2], dict)


# ── the previous-dates lookup ────────────────────────────────────────────────

class Rec:
    def __init__(self, dates):
        self.dates, self.calls = dates, []

    def __call__(self, sql, *args):
        self.calls.append((sql, args))
        return ["D"], [(d,) for d in self.dates], None


def test_the_date_lookup_binds_its_anchor_when_asked():
    rec, binds = Rec([datetime(2024, 12, 31)]), {}
    service._distinct_dates_desc(rec, "T", "RDATE", 2, before=datetime(2025, 3, 31), binds=binds)
    sql, args = rec.calls[0]
    assert "RDATE < TO_DATE(:before, 'DD-MON-YYYY')" in sql and "31-MAR-2025" not in sql
    assert binds == {"before": "31-MAR-2025"} and args == (binds,)


def test_the_date_lookup_text_is_unchanged_without_binds():
    rec = Rec([])
    service._distinct_dates_desc(rec, "T", "RDATE", 2, before=datetime(2025, 3, 31))
    sql, args = rec.calls[0]
    assert "TO_DATE('31-MAR-2025', 'DD-MON-YYYY')" in sql and args == ()


def test_the_date_lookup_fallback_query_also_gets_the_binds():
    calls = []

    def execute(sql, *args):
        calls.append((sql, args))
        return ([], [], "ORA-00933") if "FETCH FIRST" in sql else (["D"], [(datetime(2024, 12, 31),)], None)

    service._distinct_dates_desc(execute, "T", "RDATE", 2, before=datetime(2025, 3, 31), binds={"before": "x"})
    assert len(calls) == 2 and calls[0][1] == calls[1][1] == ({"before": "31-MAR-2025"},)


def test_the_return_filter_binds_its_values():
    binds: dict = {}
    sql = service._return_filter_sql({**META, "return_code": "O'Brien"}, binds)
    assert sql == "RC = :rc AND FREQ = :fv" and binds == {"rc": "O'Brien", "fv": "Q"}
    assert service._return_filter_sql({**META, "return_code": "O'Brien"}) == "RC = 'O''Brien' AND FREQ = 'Q'"


def test_previous_dates_with_binds_filter_and_still_apply_the_frequency_rule():
    rec, binds = Rec([datetime(2025, 2, 28), datetime(2024, 12, 31)]), {}
    extra = service._return_filter_sql({**META, "return_code": "R1"}, binds)
    out = service._actual_previous_dates("T", "RDATE", "31-MAR-2025", 1, rec, report_freq="Q", extra_where=extra, binds=binds)
    assert out == ["31-DEC-2024"]
    sql, args = rec.calls[0]
    assert ":rc" in sql and ":before" in sql and args == (binds,) and set(binds) == {"rc", "fv", "before"}


# ── the driver call ──────────────────────────────────────────────────────────

class _Cursor:
    description = (("A",),)
    arraysize = 0

    def __init__(self):
        self.calls = []

    def execute(self, *args):
        self.calls.append(args)

    def fetchmany(self, n):
        return [(1,)]

    def close(self):
        pass


class _Conn:
    call_timeout = None

    def __init__(self):
        self.cur = _Cursor()

    def cursor(self):
        return self.cur

    def close(self):
        pass


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()
    monkeypatch.setattr(db, "_MISSING_CONFIG", [])
    monkeypatch.setattr(db, "_acquire", lambda: (c, False))
    return c


def test_execute_query_passes_binds_to_the_cursor(conn):
    assert db.execute_query("SELECT 1 FROM T WHERE X = :x", {"x": "v"})[2] is None
    assert conn.cur.calls == [("SELECT 1 FROM T WHERE X = :x", {"x": "v"})]


def test_execute_query_without_binds_calls_the_cursor_exactly_as_before(conn):
    db.execute_query("SELECT 1 FROM T;")
    assert conn.cur.calls == [("SELECT 1 FROM T",)]
    db.execute_query("SELECT 1 FROM T", {})
    assert conn.cur.calls[-1] == ("SELECT 1 FROM T",)         # an empty dict is "no binds"


# ── through service.compute_variance, with a stand-in driver ─────────────────

@pytest.fixture
def stubbed_service(monkeypatch):
    monkeypatch.setattr(service, "_return_meta", lambda rid, ctx: {"RepFreq": "Q"})
    monkeypatch.setattr(service, "resolve_physical_table_name", lambda *a, **k: "SCH.T_DP")
    monkeypatch.setattr(service, "_trusted_tbl_path", lambda meta: "")
    monkeypatch.setattr(service, "_get_table_metadata", lambda *a, **k: {
        "filter_col": "RDATE", "comp_filter_col_names": ["ID"], "is_single": True,
        "return_code_col": "RC", "freq_col": "FREQ", "freq_val": "Q"})
    db.reset_truncation()


class Driver:
    """Answers the date lookup and the main query; records every statement and its binds."""

    def __init__(self):
        self.statements = []

    def __call__(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.startswith("SELECT * FROM"):
            return ["RDATE", "ID", "AMT"], [
                (datetime(2025, 3, 31), "A", 110), (datetime(2024, 12, 31), "A", 100)], None
        return ["RDATE"], [(datetime(2024, 12, 31),)], None


def _compute(driver):
    return service.compute_variance(
        return_id="2041", table_name="T", reporting_date="31-MAR-2025", reporting_period=1,
        execute_query_fn=driver,
    )


def test_compute_variance_with_binds_is_identical_to_the_literal_form_and_carries_no_values_in_sql(monkeypatch, stubbed_service):
    monkeypatch.setattr(cfg, "USE_BIND_VARIABLES", False)
    off = Driver()
    expected = _compute(off)

    monkeypatch.setattr(cfg, "USE_BIND_VARIABLES", True)
    on = Driver()
    got = _compute(on)

    assert got == expected and "error" not in got
    assert len(on.statements) == len(off.statements) == 2          # date lookup + main query
    for sql, params in on.statements:
        assert params and re.search(r":\w+", sql)
        assert not re.search(r"'(31-MAR-2025|31-DEC-2024|2041|Q)'", sql)      # no value inside the text
    lookup_sql, lookup_params = on.statements[0]
    assert lookup_params["rc"] == "2041" and lookup_params["fv"] == "Q" and lookup_params["before"] == "31-MAR-2025"
    main_sql, main_params = on.statements[1]
    assert main_params["d0"] == "31-MAR-2025" and main_params["d1"] == "31-DEC-2024"


def test_compute_variance_with_the_flag_off_never_passes_binds(monkeypatch, stubbed_service):
    monkeypatch.setattr(cfg, "USE_BIND_VARIABLES", False)
    driver = Driver()
    _compute(driver)
    assert all(params is None for _sql, params in driver.statements)
