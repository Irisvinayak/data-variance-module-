"""The retrieval benchmark plumbing (M-21 / M-22 / L-10 / L-11 are gated by it).

No model and no index are used: the retriever is replaced by a stand-in, so these tests
prove the loading / grading / comparison logic, not retrieval quality. Real numbers must be
produced on a machine that has the index and the embedding model.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rb = _load("retrieval_bench")
ev = _load("eval_retrieval")
Case = rb.Case


# ── grading ──────────────────────────────────────────────────────────────────

def test_grade_top1_topk_and_miss():
    case = Case("q", frozenset({"t_a"}))
    assert rb.grade(case, ["T_A", "t_b"]) == "top1"
    assert rb.grade(case, ["t_b", "t_a"]) == "topk"
    assert rb.grade(case, ["t_b", "t_c"]) == "miss"
    assert rb.grade(case, []) == "miss"


def test_any_listed_table_counts_for_a_shared_description():
    case = Case("q", frozenset({"t_a", "t_b"}))
    assert rb.grade(case, ["t_b"]) == "top1"


def test_k_limits_how_far_down_the_list_counts():
    case = Case("q", frozenset({"t_z"}))
    assert rb.grade(case, ["a", "b", "c", "t_z"], k=3) == "miss"
    assert rb.grade(case, ["a", "b", "c", "t_z"], k=4) == "topk"


# ── summary ──────────────────────────────────────────────────────────────────

def test_summary_counts_and_rates():
    s = rb.summarize(["top1", "top1", "topk", "miss"], [0.1, 0.3])
    assert s["cases"] == 4 and s["top1"] == 2 and s["topk"] == 3
    assert s["top1_rate"] == 0.5 and s["topk_rate"] == 0.75 and s["avg_ms"] == 200.0


def test_summary_of_nothing_is_an_error_not_a_perfect_score():
    with pytest.raises(ValueError):
        rb.summarize([])


# ── comparison against a baseline ────────────────────────────────────────────

BASE = {"cases": 10, "top1": 8, "topk": 9, "top1_rate": 0.8, "topk_rate": 0.9}


def test_no_regression_when_equal_or_better():
    assert rb.compare(dict(BASE), BASE) == []
    better = {**BASE, "top1": 9, "top1_rate": 0.9}
    assert rb.compare(better, BASE) == []


def test_a_drop_is_reported_for_each_metric():
    worse = {**BASE, "top1": 6, "topk": 8, "top1_rate": 0.6, "topk_rate": 0.8}
    problems = rb.compare(worse, BASE)
    assert len(problems) == 2 and "top-1" in problems[0] and "top-k" in problems[1]


def test_a_small_drop_inside_the_tolerance_is_allowed():
    slightly = {**BASE, "top1": 8, "top1_rate": 0.79}
    assert rb.compare(slightly, BASE, max_drop=0.02) == []
    assert rb.compare(slightly, BASE, max_drop=0.005) != []


def test_a_different_case_count_is_flagged():
    other = {**BASE, "cases": 12}
    assert any("case count" in p for p in rb.compare(other, BASE))


# ── case files ───────────────────────────────────────────────────────────────

def _file(tmp_path, text):
    p = tmp_path / "cases.jsonl"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_a_cases_file_is_loaded_with_comments_and_blank_lines(tmp_path):
    path = _file(tmp_path, '# comment\n\n{"query": "total loans", "expected": "T_A"}\n'
                           '{"query": "npa", "expected": ["T_B", "T_C"], "login_id": "bob"}\n')
    cases = rb.load_cases_file(path)
    assert cases[0] == Case("total loans", frozenset({"t_a"}), "iris810")
    assert cases[1].expected == frozenset({"t_b", "t_c"}) and cases[1].login_id == "bob"


@pytest.mark.parametrize("bad", ['{"query": "q"}', "not json", '{"query": "", "expected": "t"}',
                                 '{"query": "q", "expected": []}', '{"query": "q", "expected": [""]}'])
def test_a_bad_line_names_its_line_number(tmp_path, bad):
    with pytest.raises(ValueError, match=r"cases.jsonl:2"):
        rb.load_cases_file(_file(tmp_path, '{"query": "ok", "expected": "t"}\n' + bad + "\n"))


def test_an_empty_file_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="no cases"):
        rb.load_cases_file(_file(tmp_path, "# nothing\n"))


def test_the_built_in_cases_are_the_original_six():
    cases = rb.built_in_cases()
    assert len(cases) == 6 and cases[0].query.startswith("What is the total gross NPA")
    assert Case("total loan assets domestic", frozenset({"cims_raq_q_sec1_part_a_dom"}), "iris810") in cases


# ── the runner, with a stand-in retriever ────────────────────────────────────

@pytest.fixture
def stub_retriever(monkeypatch):
    import backend.nlp.retriever as retriever

    answers = {"q1": ["t_a", "t_x"], "q2": ["t_x", "t_b"], "q3": ["t_y"]}
    monkeypatch.setattr(retriever, "get_relevant_schema",
                        lambda query, ctx: {"tables": [{"table": t} for t in answers[query]]})


def _cases_file(tmp_path):
    return _file(tmp_path, '{"query": "q1", "expected": "t_a"}\n{"query": "q2", "expected": "t_b"}\n'
                           '{"query": "q3", "expected": "t_a"}\n')


def test_the_runner_grades_saves_and_exits_1_on_a_miss(stub_retriever, tmp_path, capsys):
    out = tmp_path / "base.json"
    code = ev.main(["--cases-file", _cases_file(tmp_path), "--json", str(out), "--quiet"])
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert (saved["cases"], saved["top1"], saved["topk"]) == (3, 1, 2)
    assert code == 1                                          # one case missed entirely (old exit-code meaning)
    assert "MISS" in capsys.readouterr().out


def test_the_runner_passes_against_its_own_baseline_and_fails_against_a_better_one(stub_retriever, tmp_path):
    cases = _cases_file(tmp_path)
    base = tmp_path / "base.json"
    ev.main(["--cases-file", cases, "--json", str(base), "--quiet"])
    assert ev.main(["--cases-file", cases, "--baseline", str(base), "--quiet"]) == 0
    better = tmp_path / "better.json"
    better.write_text(json.dumps({"cases": 3, "top1": 3, "topk": 3, "top1_rate": 1.0, "topk_rate": 1.0}), encoding="utf-8")
    assert ev.main(["--cases-file", cases, "--baseline", str(better), "--quiet"]) == 1
    assert ev.main(["--cases-file", cases, "--baseline", str(better), "--max-drop", "0.7", "--quiet"]) == 0


def test_the_default_run_still_uses_the_built_in_cases():
    args = ev.parse_args([])
    assert len(ev.select_cases(args)) == 6
