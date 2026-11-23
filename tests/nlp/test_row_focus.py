from backend.nlp.row_focus import content_words, focus_rows, matching_rows

Q = "Compare Representative Office expenses across all countries for the quarter"


def _rows(*labels):
    return [{"display_label": l, "identifier": str(i)} for i, l in enumerate(labels)]


ROWS = _rows(
    "AU - Australia_a. Rep Office expenses",
    "AU - Australia_Total Inflows ( 1+2+3+4)",
    "ALL Countries_Total Outflows (1+2+3+4+5+6)",
    "AU - Australia_Net Inflows (Total Inflows -Total Outflows)",
    "ALL Countries_a. Rep Office expenses",
    "UK - Britain_a. Rep Office expenses",
)


def test_generic_words_dropped_and_content_kept():
    assert content_words(Q) == ["representative", "office", "expense"]


def test_rep_matches_representative_and_keeps_one_row_per_country():
    keep, _ = matching_rows(Q, ROWS)
    assert keep == [0, 4, 5]


def test_unknown_word_means_no_filter():
    assert matching_rows("Compare provisions for write offs across all countries", ROWS)[0] is None


def test_all_rows_matching_means_no_filter():
    assert matching_rows("office expenses", _rows("Rep Office expenses", "HO Office expenses"))[0] is None


def test_empty_phrase_no_filter():
    assert matching_rows("compare all countries for the latest quarter", ROWS)[0] is None


def test_focus_rows_adds_note():
    out, note = focus_rows({"rows": ROWS}, Q)
    assert len(out["rows"]) == 3 and "3 of 6" in note
