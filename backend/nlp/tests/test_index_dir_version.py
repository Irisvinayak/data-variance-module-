"""VERSION selects the embedding index folder.

5.5's index covers the CIMS returns, 6.0's the QCB returns. They share no
tables, so serving one host from the other's index does not fail loudly:
retrieval returns the closest wrong table and the generated SQL names something
that does not exist for that host. Before this was version-aware, VERSION=6.0
silently kept reading the 5.5 index - which is exactly that failure.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(  # repo root: nlp/tests -> nlp -> backend -> root
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.nlp import nlp_config  # noqa: E402


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def test_each_version_resolves_to_its_own_folder():
    assert nlp_config.index_dir_for("5.5") != nlp_config.index_dir_for("6.0")


def test_5_5_defaults_to_backend_output():
    assert nlp_config.index_dir_for("5.5") == os.path.join(
        nlp_config.BACKEND_DIR, "output"
    )


def test_6_0_defaults_to_backend_output_6_0():
    assert nlp_config.index_dir_for("6.0") == os.path.join(
        nlp_config.BACKEND_DIR, "output6.0"
    )


@pytest.mark.parametrize("raw,expected", [
    ("5", "5.5"), ("5.5", "5.5"), ("6", "6.0"), ("6.0", "6.0"), ("6.0.1", "6.0"),
])
def test_version_spellings_normalise_the_same_way_as_the_rest_of_the_app(raw, expected):
    """One normalisation rule for the whole app - `index_dir_for` delegates to
    `is_legacy_mode` rather than parsing VERSION itself."""
    assert nlp_config.index_dir_for(raw) == nlp_config.index_dir_for(expected)


def test_configured_index_dir_matches_the_configured_version():
    from backend.config import APP_VERSION
    assert nlp_config.INDEX_DIR == nlp_config.index_dir_for(APP_VERSION)


def test_every_artifact_path_sits_under_index_dir():
    for path in (
        nlp_config.SCHEMA_JSON_PATH, nlp_config.TABLE_INDEX_PATH,
        nlp_config.TABLE_META_PATH, nlp_config.COLUMN_INDEX_PATH,
        nlp_config.ROW_LABEL_INDEX_PATH, nlp_config.BM25_INDEX_PATH,
        nlp_config.QA_PAIRS_PATH,
    ):
        assert path.startswith(nlp_config.INDEX_DIR), path


# --------------------------------------------------------------------------
# the override must keep winning
# --------------------------------------------------------------------------

def test_an_explicit_override_wins_for_either_version(monkeypatch):
    """Deployments already pinning DV_NLP_INDEX_DIR must be unaffected by this
    becoming version-aware."""
    monkeypatch.setattr(nlp_config, "INDEX_DIR_OVERRIDE", r"D:\pinned\somewhere")
    assert nlp_config.index_dir_for("5.5") == r"D:\pinned\somewhere"
    assert nlp_config.index_dir_for("6.0") == r"D:\pinned\somewhere"


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------

def test_no_problem_reported_when_the_folder_is_populated():
    assert nlp_config.index_dir_problem() == ""


def test_a_missing_folder_names_the_version_and_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setattr(nlp_config, "INDEX_DIR", str(tmp_path / "nope"))
    problem = nlp_config.index_dir_problem()

    assert "VERSION=" in problem
    assert "DV_NLP_INDEX_DIR_" in problem


def test_a_folder_without_schema_json_is_not_mistaken_for_an_index(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(nlp_config, "INDEX_DIR", str(tmp_path))
    monkeypatch.setattr(nlp_config, "SCHEMA_JSON_PATH",
                        str(tmp_path / "schema.json"))
    assert "no schema.json" in nlp_config.index_dir_problem()


# --------------------------------------------------------------------------
# the two indexes really are different data
# --------------------------------------------------------------------------

def _tables(index_dir: str) -> set:
    path = os.path.join(index_dir, "schema.json")
    if not os.path.isfile(path):
        pytest.skip("no index built at " + index_dir)
    with open(path, encoding="utf-8") as fh:
        return {(t.get("table") or "").lower() for t in json.load(fh)}


def test_the_two_indexes_share_no_tables():
    """This is why the switch matters. If they overlapped, pointing a host at
    the wrong one would merely degrade ranking; because they are disjoint, every
    generated query would name a table the host's database does not have."""
    five, six = _tables(nlp_config.index_dir_for("5.5")), _tables(
        nlp_config.index_dir_for("6.0")
    )
    assert five and six
    assert five & six == set()


def test_the_6_0_index_holds_the_qcb_tables():
    tables = _tables(nlp_config.index_dir_for("6.0"))
    assert all(t.startswith("qcb_") for t in tables), sorted(tables)[:5]
    # And under their real names - Oracle has no _DP variants of these.
    assert not any(t.endswith("_dp") for t in tables)
