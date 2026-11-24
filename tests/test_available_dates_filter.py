"""get_available_dates must offer only dates that have rows for THIS return.

compute_variance() reads a shared table through the return-code / frequency predicate
(_return_filter_sql). The date list used to skip it, so a return could be offered dates
that only another return (or frequency) had filed, and picking one ended in
"No data found ... Dates available in DB: <the one date it really had>".
"""
from datetime import datetime

from backend.data import service


def _dates_sql(monkeypatch, table_meta, return_id="2037"):
    sqls: list[str] = []
    monkeypatch.setattr(service, "_return_meta", lambda rid, ctx: {"RepFreq": "M", "TblPath": "x"})
    monkeypatch.setattr(service, "_trusted_tbl_path", lambda meta: "x")
    monkeypatch.setattr(service, "_get_table_metadata", lambda *a, **k: table_meta)
    monkeypatch.setattr(service, "resolve_physical_table_name", lambda *a, **k: "CRILC.T_DP")

    def fake_execute(sql, *args):
        sqls.append(sql)
        return ["RDATE"], [(datetime(2025, 3, 31),)], None

    dates = service.get_available_dates(return_id, "T", fake_execute)
    return dates, sqls[0]


def test_a_shared_table_is_limited_to_this_returns_rows(monkeypatch):
    meta = {
        "filter_col": "RDATE", "is_single": True, "return_code_col": "RETURN_CODE",
        "freq_col": "FREQ", "freq_val": "M",
    }
    dates, sql = _dates_sql(monkeypatch, meta)
    assert dates == ["31-MAR-2025"]
    assert "WHERE" in sql and "RETURN_CODE =" in sql and "2037" in sql
    assert "FREQ =" in sql


def test_a_table_with_no_return_filter_is_read_unfiltered(monkeypatch):
    dates, sql = _dates_sql(monkeypatch, {"filter_col": "RDATE"})
    assert dates == ["31-MAR-2025"]
    assert "WHERE" not in sql
