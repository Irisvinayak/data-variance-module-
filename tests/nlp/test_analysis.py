from backend.nlp.analysis import analyze
from backend.nlp.operation_parser import parse_operation


def _metric(prev):
    return {"value": prev}


def _result(data):
    """data: {label: (prev, curr)} for one column AMT."""
    rows = [{
        "identifier": k, "display_label": k,
        "current": {"AMT": curr},
        "previous": {"previous_1": {"AMT": _metric(prev)}},
    } for k, (prev, curr) in data.items()]
    return {"columns": ["AMT"], "rows": rows}


DATA = {
    "A": ("100", "150"),    # +50  / +50%
    "B": ("200", "180"),    # -20  / -10%
    "C": ("300", "300"),    # unchanged
    "D": ("0", "40"),       # new from zero
    "E": ("500", "0"),      # became zero
    "F": ("1000", "1005"),  # +5 / +0.5%
}


def run(q, data=DATA):
    return analyze(_result(data), parse_operation(q))


def ids(res):
    return [r["identifier"] for r in res["rows"]]


def test_plain_compare_keeps_rows_and_summarises():
    res = run("Compare the current data with the previous period.")
    assert len(res["rows"]) == 6
    assert res["analysis"]["counts"] == {
        "increased": 2, "decreased": 1, "unchanged": 1, "new": 1, "became_zero": 1}
    assert "2 increased" in res["summary"]


def test_increase_only():
    assert set(ids(run("Show only increased values."))) == {"A", "D", "F"}


def test_decrease_only():
    assert set(ids(run("Show only decreased values."))) == {"B", "E"}


def test_pct_threshold_excludes_undefined_and_reports_it():
    res = run("Show variations greater than 10%.")
    assert set(ids(res)) == {"A", "E"}           # B is exactly -10%: not "more than"
    assert any("rose from zero" in n for n in res["analysis_notes"])


def test_abs_threshold():
    assert set(ids(run("Show absolute changes above 100"))) == {"E"}


def test_bare_number_threshold_is_flagged_ambiguous():
    res = run("Show changes above 100")
    assert res["analysis"]["effective_measure"] == "pct"
    assert any("read as percentage" in n for n in res["analysis_notes"])


def test_signed_negative_threshold():
    assert set(ids(run("Identify changes below -15%."))) == {"E"}


def test_between_is_inclusive():
    assert set(ids(run("Show variations between 10% and 50%."))) == {"A", "B"}


def test_decrease_with_threshold():
    assert set(ids(run("Show elements where the value decreased by more than 5%."))) == {"B", "E"}


def test_top_n_by_abs_increase():
    res = run("Which element has the highest absolute increase?")
    assert ids(res) == ["A"]                    # +50 beats D's +40
    res = run("Give me the top 2 absolute increases.")
    assert ids(res) == ["A", "D"]               # +50, +40


def test_top_decreases_sorted_most_negative_first():
    res = run("Give me the top 2 absolute decreases.")
    assert ids(res) == ["E", "B"]


def test_ambiguous_rank_defaults_to_pct_with_note():
    res = run("Show top 2 elements with highest variation.")
    assert ids(res) == ["E", "A"]               # -100%, +50%; D has no pct
    assert any("read as percentage" in n for n in res["analysis_notes"])


def test_bottom_n():
    res = run("Show the bottom 1 elements by absolute variation.")
    assert ids(res) == ["C"]


def test_unchanged():
    assert ids(run("Show unchanged values.")) == ["C"]


def test_new_and_became_zero():
    assert ids(run("Show elements where previous value was zero and current value is non-zero.")) == ["D"]
    assert ids(run("Which elements became zero?")) == ["E"]


def test_column_wise_ranking_keeps_all_rows():
    data = _result(DATA)
    data["columns"] = ["AMT", "OTHER"]
    for r in data["rows"]:
        r["current"]["OTHER"] = "10"
        r["previous"]["previous_1"]["OTHER"] = _metric("10")
    res = analyze(data, parse_operation("Which column has the highest absolute variation?"))
    assert [c["column"] for c in res["column_summary"]] == ["AMT"]


def test_no_comparison_data_returns_input_unchanged():
    res = {"columns": ["AMT"], "rows": [{"identifier": "A", "current": {"AMT": "1"}, "previous": {}}]}
    assert analyze(res, parse_operation("Show only increased values.")) is res


def test_non_numeric_cells_are_skipped():
    res = run("Show only increased values.", {"A": ("x", "y"), "B": ("1", "2")})
    assert ids(res) == ["B"]
