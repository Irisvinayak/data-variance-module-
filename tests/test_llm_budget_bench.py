"""M-19: cut-off detection and the budget benchmark's arithmetic. A fake model only; these tests say
nothing about what any real model does at any cap - that needs scripts/bench_llm_budget.py on a
machine with the model."""

from __future__ import annotations

import importlib.util
import logging
import pathlib

import pytest

from backend import metrics
from backend.nlp import intent_resolver, sql_generator
from backend.nlp.llm_guard import note_if_cut_off

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench_llm_budget", ROOT / "scripts" / "bench_llm_budget.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.fixture(autouse=True)
def _clean():
    metrics.reset()
    yield
    metrics.reset()


# ── cut-off detection in the app ─────────────────────────────────────────────

def test_a_reply_stopped_by_the_cap_is_logged_and_counted(caplog):
    with caplog.at_level(logging.WARNING):
        assert note_if_cut_off("sql_generator", {"response": "SELECT a,", "done_reason": "length"}, "m", 1) is True
    assert "token cap" in caplog.text and "DV_SQL_NUM_PREDICT" in caplog.text
    assert metrics.snapshot()["counters"]["dv_llm_truncated_total"][(("caller", "sql_generator"),)] == 1


@pytest.mark.parametrize("body", [{"done_reason": "stop"}, {"response": "x"}, {}, None, "text", []])
def test_a_normal_or_odd_reply_is_not_flagged(body):
    assert note_if_cut_off("sql_generator", body, "m", 1) is False
    assert "dv_llm_truncated_total" not in metrics.snapshot()["counters"]


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def test_the_sql_call_still_returns_the_text_unchanged_when_it_was_cut_off(monkeypatch):
    monkeypatch.setattr(sql_generator, "_breaker", sql_generator.CircuitBreaker(60))
    session = type("S", (), {"post": staticmethod(lambda url, json=None, timeout=None: _Resp(
        {"response": "SELECT a, b,", "done_reason": "length"}))})()
    monkeypatch.setattr(sql_generator, "_session", session)
    text, _ms = sql_generator._call_ollama("p", "m", {}, attempt=1)
    assert text == "SELECT a, b,"                              # observation only: the reply is not altered
    assert metrics.snapshot()["counters"]["dv_llm_truncated_total"][(("caller", "sql_generator"),)] == 1


def test_the_intent_call_reports_a_cut_off_reply_too(monkeypatch):
    session = type("S", (), {"post": staticmethod(lambda url, json=None, timeout=None: _Resp(
        {"response": '{"return_id": "1", "ta', "done_reason": "length"}))})()
    monkeypatch.setattr(intent_resolver, "_session", session)
    text, _ms = intent_resolver._call_ollama("p", attempt=1)
    assert text.startswith('{"return_id"')
    assert metrics.snapshot()["counters"]["dv_llm_truncated_total"][(("caller", "intent_resolver"),)] == 1


def test_no_intent_limit_was_changed_by_this_work():
    from backend.nlp import nlp_config

    assert nlp_config.INTENT_NUM_PREDICT == 200


# ── the benchmark arithmetic ─────────────────────────────────────────────────

def fake(cut_below):
    """A model that cuts a reply off whenever the cap is below `cut_below`."""
    def call(question, cap):
        if question == "boom":
            raise RuntimeError("down")
        return {"response": "x" * min(cap, 300), "done_reason": "length" if cap < cut_below else "stop", "latency_ms": cap / 2}
    return call


def test_sweep_counts_cut_off_replies_per_cap():
    result = bench.sweep(["q1", "q2", "q3", "q4"], [128, 256, 512], fake(200))
    assert result[128]["cut_off"] == 4 and result[128]["cut_off_rate"] == 1.0
    assert result[256]["cut_off"] == 0 and result[512]["cut_off_rate"] == 0.0
    assert result[128]["answered"] == 4 and result[128]["avg_reply_chars"] == 128.0


def test_failed_calls_are_counted_separately_and_do_not_skew_the_rate():
    result = bench.sweep(["q1", "boom", "q2"], [128], fake(200))
    assert result[128]["failed"] == 1 and result[128]["answered"] == 2 and result[128]["cut_off_rate"] == 1.0


def test_all_calls_failing_gives_no_rate_not_a_zero():
    result = bench.sweep(["boom"], [128], fake(200))
    assert result[128]["cut_off_rate"] is None and result[128]["p50_ms"] is None


def test_latency_percentiles():
    assert bench.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
    assert bench.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
    assert bench.percentile([7], 0.95) == 7
    with pytest.raises(ValueError):
        bench.percentile([], 0.5)


def test_the_recommendation_is_the_smallest_cap_without_cut_offs():
    result = bench.sweep(["q1", "q2"], [128, 256, 512], fake(200))
    assert bench.recommend(result) == 256
    assert bench.recommend(bench.sweep(["q1"], [64, 128], fake(10_000))) is None
    assert bench.recommend(bench.sweep(["q1", "q2"], [128, 256], fake(200)), max_cut_off_rate=1.0) == 128


def test_sweep_refuses_an_empty_input():
    with pytest.raises(ValueError):
        bench.sweep([], [128], fake(1))
    with pytest.raises(ValueError):
        bench.sweep(["q"], [], fake(1))


def test_question_files(tmp_path):
    f = tmp_path / "q.txt"
    f.write_text("# comment\n\nfirst question\n  second one  \n", encoding="utf-8")
    assert bench.read_questions(str(f)) == ["first question", "second one"]
    empty = tmp_path / "e.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no questions"):
        bench.read_questions(str(empty))
