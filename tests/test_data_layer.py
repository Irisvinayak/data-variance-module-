"""Regression tests for the data-layer and API error-mapping fixes."""

from datetime import datetime

from fastapi import HTTPException

from backend.api.errors import http_error
from backend.data import service
from backend.data.calculate_variance import _is_excluded_value_col, build_query, sql_literal


def test_sql_literal_doubles_quotes():
    assert sql_literal("2029") == "'2029'"
    assert sql_literal("x' OR '1'='1") == "'x'' OR ''1''=''1'"


def test_build_query_escapes_return_code():
    sql = build_query(
        "T",
        {"filter_col": "RDATE", "is_single": True, "return_code_col": "RC"},
        datetime(2025, 3, 31), [], "1' OR '1'='1",
    )
    assert "RC = '1'' OR ''1''=''1'" in sql


def test_excluded_value_cols_ignore_punctuation():
    for col in ("SR_NO", "Sr.No", "SLNO.", "ROW_NUM", "s_no"):
        assert _is_excluded_value_col(col)
    assert not _is_excluded_value_col("AMOUNT")


def test_distinct_dates_rownum_fallback_dedupes_inside_the_cap():
    seen = []

    def execute(sql):
        seen.append(sql)
        if "FETCH FIRST" in sql:
            return [], [], "ORA-00933"
        return ["RDATE"], [(datetime(2025, 3, 31),)], None

    rows, err = service._distinct_dates_desc(execute, "T", "RDATE", 10)
    assert err is None and rows
    # DISTINCT and ORDER BY must be applied before ROWNUM caps the rows.
    assert seen[1] == (
        "SELECT RDATE FROM (SELECT DISTINCT RDATE FROM T ORDER BY RDATE DESC) "
        "WHERE ROWNUM <= 10"
    )


def test_distinct_dates_before_filter():
    seen = []

    def execute(sql):
        seen.append(sql)
        return [], [], None

    service._distinct_dates_desc(execute, "T", "RDATE", 2, before=datetime(2025, 3, 31))
    assert "WHERE RDATE < TO_DATE('31-MAR-2025', 'DD-MON-YYYY')" in seen[0]


def test_http_error_passes_http_exceptions_through():
    exc = HTTPException(status_code=403, detail="denied")
    assert http_error(exc, "/x") is exc


def test_http_error_mapping_status_codes_and_no_leaks():
    """Status codes are the contract; the detail text must never carry internals
    (code review SEC-04): exception text can hold paths, SQL, ORA text, tenants."""
    cases = [
        (FileNotFoundError(r"D:\Repo6\1001\secret.xml"), 404),
        (KeyError("TABLE_X"), 404),
        (RuntimeError("ORA-00942 CRILC.TABLE_DP"), 500),
        (ValueError("bad"), 500),
    ]
    for exc, code in cases:
        out = http_error(exc, "/x")
        assert out.status_code == code
        for fragment in ("Repo6", "secret.xml", "TABLE_X", "ORA-", "CRILC", "bad"):
            assert fragment not in out.detail, (type(exc).__name__, out.detail)
    assert "Reference:" in http_error(ValueError("bad"), "/x").detail


# ── M-06: every identifier spliced into SQL is shape-checked ─────────────────

import pytest  # noqa: E402

from backend.data.calculate_variance import is_safe_sql_ref  # noqa: E402

_META = {"filter_col": "RDATE", "is_single": True, "return_code_col": "RC", "freq_col": "FREQ", "freq_val": "Q"}


@pytest.mark.parametrize("ref", ["RDATE", "T", "SCH.TABLE_DP", "A$B#1", "CRILC.CIMS_RAQ_Q_SEC1_PART_A_DOM", "_X"])
def test_real_shaped_references_are_accepted(ref):
    assert is_safe_sql_ref(ref)


@pytest.mark.parametrize("ref", [
    "", None, 5, "A B", "A;DROP TABLE X", "A--", 'A"B', "A'B", "A)", "(A", "A/*x*/", "1ABC", "A..B", ".A", "A.", "A\nB",
    "A.B.C.D", "A" * 300,
])
def test_hostile_or_malformed_references_are_rejected(ref):
    assert not is_safe_sql_ref(ref)


@pytest.mark.parametrize("field,value", [
    ("filter_col", "RDATE) OR 1=1 --"),
    ("return_code_col", "RC = 1 OR 1"),
    ("freq_col", "FREQ; DROP TABLE X"),
])
def test_build_query_refuses_unsafe_metadata_names(field, value):
    meta = {**_META, field: value}
    with pytest.raises(ValueError, match="Unsafe"):
        build_query("T", meta, datetime(2025, 3, 31), [], "RC1")


def test_build_query_refuses_an_unsafe_table_name():
    with pytest.raises(ValueError, match="Unsafe table"):
        build_query("T WHERE 1=1 --", _META, datetime(2025, 3, 31), [], "RC1")


def test_build_query_output_for_good_names():
    """M-17: each date is a half-open day range, so a DATE with a time part is still found."""
    sql = build_query("SCH.T", _META, datetime(2025, 3, 31), [datetime(2024, 12, 31)], "RC1")
    day = lambda d: (  # noqa: E731
        f"(RDATE >= TO_DATE('{d}', 'DD-MON-YYYY') AND RDATE < TO_DATE('{d}', 'DD-MON-YYYY') + 1)"
    )
    assert sql == (
        f"SELECT * FROM SCH.T WHERE ({day('31-MAR-2025')} OR {day('31-DEC-2024')}) "
        "AND RC = 'RC1' AND FREQ = 'Q'"
    )


def test_unused_columns_are_not_checked():
    # return_code_col is only spliced in for single-return tables.
    build_query("T", {"filter_col": "RDATE", "is_single": False, "return_code_col": "x y"},
                datetime(2025, 3, 31), [], "RC1")


def test_distinct_dates_refuses_an_unsafe_filter_column():
    with pytest.raises(ValueError, match="Unsafe"):
        service._distinct_dates_desc(lambda sql: ([], [], None), "T", "RDATE; DROP TABLE T", 5)
