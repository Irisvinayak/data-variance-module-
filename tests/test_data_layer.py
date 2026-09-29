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


def test_http_error_mapping_unchanged():
    assert http_error(FileNotFoundError("gone"), "/x").status_code == 404
    assert http_error(KeyError("k"), "/x").status_code == 404
    assert http_error(RuntimeError("boom"), "/x").status_code == 500
    generic = http_error(ValueError("bad"), "/x")
    assert generic.status_code == 500
    assert generic.detail == "Unexpected server error: ValueError: bad"
