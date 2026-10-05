"""L-15: the deliberate `except Exception` fallbacks, each exercised once.

Every one of these blocks exists so a secondary failure degrades a feature instead
of failing the request. A fallback that is never run is a fallback nobody knows
works, so each test forces the failure and checks (a) nothing escapes and (b) the
documented degraded result is what comes back.
"""

from __future__ import annotations

import logging
import pickle
import time
from datetime import date, datetime

import pytest

from backend import ai_audit
from backend.config import RequestContext
from backend.data import calculate_variance as cv
from backend.data import service
from backend.nlp import date_resolver, lexical_search, return_lookup, schema_info

CTX = RequestContext(login_id="u", tenant_id="1001")


# ── calculate_variance: numeric helpers fall back instead of raising ─────────

@pytest.mark.parametrize("prev,curr", [("abc", "xyz"), (None, "x"), ("n/a", 5), (object(), object())])
def test_difference_helpers_never_raise_on_non_numeric_input(prev, curr):
    cv.get_difference(prev, curr)
    cv.get_pct_change(prev, curr)
    cv.get_variance_summary(prev, curr)


def test_difference_of_unequal_text_shows_the_new_text_and_equal_text_shows_zero():
    assert cv.get_difference("Yes", "No") == {"value": "No", "color": ""}
    assert cv.get_difference("Yes", " yes ") == {"value": "0.00", "color": ""}


def test_pct_and_summary_are_none_for_text():
    assert cv.get_pct_change("a", "b") is None
    assert cv.get_variance_summary("a", "b") is None


def test_an_object_whose_str_fails_gives_none_not_an_exception():
    class Bad:
        def __str__(self):
            raise RuntimeError("no str")

    assert cv.get_difference(Bad(), Bad()) is None


def _calc(**kw):
    base = dict(
        return_code="2041", table_name="T", reporting_date="31-MAR-2025",
        get_table_metadata_fn=lambda *a, **k: {"filter_col": "RDATE", "comp_filter_col_names": [], "is_single": False},
        execute_query_fn=lambda q, *a: [],
    )
    base.update(kw)
    return cv.calculate_variance(**base)


def test_an_unparseable_reporting_date_is_an_error_result_not_an_exception():
    out = _calc(reporting_date="not-a-date")
    assert "error" in out and cv.result_error(out)


def test_an_unparseable_comparison_date_is_an_error_result():
    out = _calc(comparison_dates=["31-MAR-2025", "garbage"])
    assert cv.result_error(out)


def test_a_failing_query_gives_the_fixed_sentence_and_no_oracle_text(caplog):
    def boom(q, *a):
        raise RuntimeError("ORA-00942: table CRILC.SECRET does not exist")

    with caplog.at_level(logging.ERROR):
        out = _calc(execute_query_fn=boom, comparison_dates=["31-MAR-2025"])
    assert cv.result_error(out) == "The data could not be retrieved. Please try again or contact support."
    assert "ORA-00942" in caplog.text                       # detail stays in the log


# ── service: table diagnostics are best-effort ───────────────────────────────

def test_table_diagnostics_swallow_every_failure(caplog):
    def boom(sql):
        raise RuntimeError("connection lost")

    with caplog.at_level(logging.WARNING):
        service._run_table_diagnostics("T", "RDATE", boom)        # must not raise
    assert "Step1 EXCEPTION" in caplog.text and "Step2 EXCEPTION" in caplog.text


def test_table_diagnostics_report_a_query_error_without_raising(caplog):
    with caplog.at_level(logging.WARNING):
        service._run_table_diagnostics("T", "RDATE", lambda sql: ([], [], "ORA-12541"))
    assert "could not count rows" in caplog.text


# ── date_resolver: falls back to a direct scan ───────────────────────────────

def test_candidate_dates_fall_back_to_the_direct_scan_when_the_service_list_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("table mapping quirk")

    monkeypatch.setattr("backend.data.service.get_available_dates", boom)
    monkeypatch.setattr(
        date_resolver, "_available_dates_desc",
        lambda *a, **k: [datetime(2025, 3, 31), datetime(2024, 12, 31)],
    )
    assert date_resolver._candidate_dates("2041", "T", "RDATE", CTX) == [date(2025, 3, 31), date(2024, 12, 31)]


def test_candidate_dates_fall_back_when_the_service_list_is_empty(monkeypatch):
    monkeypatch.setattr("backend.data.service.get_available_dates", lambda *a, **k: [])
    monkeypatch.setattr(date_resolver, "_available_dates_desc", lambda *a, **k: [datetime(2025, 3, 31)])
    assert date_resolver._candidate_dates("2041", "T", "RDATE", CTX) == [date(2025, 3, 31)]


# ── lexical_search: a broken BM25 index costs ranking quality, not the query ─

def test_a_corrupt_bm25_index_degrades_to_no_signal_and_warns_once(tmp_path, caplog, monkeypatch):
    bad = tmp_path / "bm25.pkl"
    bad.write_bytes(b"this is not a pickle")
    monkeypatch.setattr(lexical_search, "_bm25_cache", {})
    monkeypatch.setattr(lexical_search, "_bm25_failed", set())
    with caplog.at_level(logging.WARNING):
        assert lexical_search.search_bm25(str(bad), "loans", 5) == []
        assert lexical_search.search_bm25(str(bad), "loans", 5) == []
    assert caplog.text.count("could not be loaded") == 1


def test_a_pickle_missing_its_keys_also_degrades(tmp_path, monkeypatch):
    bad = tmp_path / "bm25.pkl"
    bad.write_bytes(pickle.dumps({"unexpected": 1}))
    monkeypatch.setattr(lexical_search, "_bm25_cache", {})
    monkeypatch.setattr(lexical_search, "_bm25_failed", set())
    assert lexical_search.search_bm25(str(bad), "loans", 5) == []


def test_a_missing_bm25_file_is_a_silent_noop(tmp_path):
    assert lexical_search.search_bm25(str(tmp_path / "absent.pkl"), "loans", 5) == []


# ── return_lookup ────────────────────────────────────────────────────────────

def test_a_failed_background_rebuild_keeps_the_stale_cache_and_clears_its_flag(monkeypatch, caplog):
    key = CTX.tenant_id
    stale = {"t": [{"return_id": "1"}]}
    monkeypatch.setattr(return_lookup, "_cache", {key: stale})
    monkeypatch.setattr(return_lookup, "_cache_ts", {key: time.monotonic() - 99999})
    monkeypatch.setattr(return_lookup, "_rebuild_in_progress", {key})

    def boom(ctx):
        raise RuntimeError("repository unreachable")

    monkeypatch.setattr(return_lookup, "_build_lookup", boom)
    with caplog.at_level(logging.ERROR):
        return_lookup._rebuild_in_background(CTX)
    assert return_lookup._cache[key] is stale
    assert key not in return_lookup._rebuild_in_progress           # a later rebuild may be attempted
    assert "keeping stale cache" in caplog.text


def test_a_successful_background_rebuild_swaps_the_cache(monkeypatch):
    key = CTX.tenant_id
    fresh = {"t2": []}
    monkeypatch.setattr(return_lookup, "_cache", {key: {}})
    monkeypatch.setattr(return_lookup, "_cache_ts", {key: 0.0})
    monkeypatch.setattr(return_lookup, "_rebuild_in_progress", {key})
    monkeypatch.setattr(return_lookup, "_build_lookup", lambda ctx: fresh)
    return_lookup._rebuild_in_background(CTX)
    assert return_lookup._cache[key] is fresh


# ── schema_info: row labels are optional vocabulary ──────────────────────────

def test_vocabulary_survives_unreadable_row_label_metadata(monkeypatch, caplog):
    monkeypatch.setattr(schema_info, "_vocab_cache", None)
    monkeypatch.setattr(schema_info, "_vocab_mtime", -1.0)

    def boom(*a, **k):
        raise OSError("row label index unreadable")

    monkeypatch.setattr("backend.nlp.index_store.all_meta", boom)
    with caplog.at_level(logging.WARNING):
        vocab = schema_info.data_vocabulary()
    assert isinstance(vocab, frozenset)
    assert "Could not read row labels" in caplog.text


# ── ai_audit: the audit trail can never take a request down ──────────────────

class _ExplodingHandler:
    def handle(self, record):
        raise OSError("disk full")


def test_audit_write_failure_is_swallowed_and_warned_once(monkeypatch, caplog):
    monkeypatch.setattr(ai_audit, "_get_handler", lambda: _ExplodingHandler())
    monkeypatch.setattr(ai_audit, "_warned", False)
    with caplog.at_level(logging.WARNING):
        ai_audit.record(caller="t", model="m", query="q")
        ai_audit.record(caller="t", model="m", query="q")
    assert caplog.text.count("failed to write an audit record") == 1


def test_an_unopenable_audit_log_disables_auditing_without_raising(monkeypatch, tmp_path, caplog):
    def boom(*a, **k):
        raise PermissionError("read-only share")

    monkeypatch.setattr(ai_audit, "_handler", None)
    monkeypatch.setattr(ai_audit, "_warned", False)
    monkeypatch.setattr(ai_audit, "_JsonlHandler", boom)
    with caplog.at_level(logging.WARNING):
        assert ai_audit._get_handler() is None
        ai_audit.record(caller="t", model="m")                      # still silent
    assert "cannot open the AI audit log" in caplog.text


def test_a_non_serialisable_audit_field_degrades_to_its_repr(monkeypatch):
    lines = []

    class Capture:
        def handle(self, record):
            lines.append(record.getMessage())

    monkeypatch.setattr(ai_audit, "_get_handler", lambda: Capture())
    ai_audit.record(caller="t", model="m", weird=object())
    assert lines and "object object" in lines[0]
