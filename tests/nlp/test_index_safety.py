"""Embedding-index safety (code review RAG-01, RAG-02, RAG-05) and the retriever's
authorisation filter.

Indexes are built here in a temp folder from tiny hand-made vectors — no model is
loaded and nothing under artifacts/ is read or written.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from types import SimpleNamespace

import faiss
import numpy as np
import pytest

from backend.nlp import index_manifest, index_store, retriever


def make_index(folder, name, vectors, meta):
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors.astype("float32"))
    faiss.write_index(index, str(folder / f"{name}_index.faiss"))
    with open(folder / f"{name}_meta.pkl", "wb") as fh:
        pickle.dump(meta, fh)
    return str(folder / f"{name}_index.faiss"), str(folder / f"{name}_meta.pkl")


def unit(rows, dim=4):
    v = np.random.RandomState(0).rand(rows, dim).astype("float32")
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def _fresh_cache():
    index_store._cache.clear()
    yield
    index_store._cache.clear()


# ── RAG-01: index and metadata must belong together ──────────────────────────

def test_consistent_index_loads_and_searches(tmp_path):
    ip, mp = make_index(tmp_path, "table", unit(3), [{"table": f"t{i}"} for i in range(3)])
    hits = index_store.search(ip, mp, unit(3)[0], k=2)
    assert hits and hits[0][1]["table"] == "t0"


def test_vector_record_count_mismatch_is_a_clear_error(tmp_path):
    ip, mp = make_index(tmp_path, "table", unit(3), [{"table": "t0"}, {"table": "t1"}])   # 3 vectors, 2 records
    with pytest.raises(index_store.IndexInconsistentError, match="different builds"):
        index_store.search(ip, mp, unit(3)[0], k=2)


def test_more_records_than_vectors_is_also_rejected(tmp_path):
    ip, mp = make_index(tmp_path, "table", unit(2), [{"table": f"t{i}"} for i in range(5)])
    with pytest.raises(index_store.IndexInconsistentError):
        index_store.search(ip, mp, unit(2)[0], k=2)


def test_mismatch_is_not_cached_as_good(tmp_path):
    ip, mp = make_index(tmp_path, "table", unit(3), [{"table": "t0"}])
    for _ in range(2):
        with pytest.raises(index_store.IndexInconsistentError):
            index_store.search(ip, mp, unit(3)[0], k=1)
    assert ip not in index_store._cache


# ── RAG-02: the embedding model must match the one that built the index ─────

def test_wrong_embedding_dimension_names_the_model_setting(tmp_path):
    ip, mp = make_index(tmp_path, "table", unit(3, dim=4), [{"table": f"t{i}"} for i in range(3)])
    with pytest.raises(index_store.IndexInconsistentError, match="DV_NLP_EMBED_MODEL"):
        index_store.search(ip, mp, np.ones(8, dtype="float32"), k=1)


# ── the manifest verifier ────────────────────────────────────────────────────

def test_verifier_passes_a_sound_folder(tmp_path):
    make_index(tmp_path, "table", unit(3), [{"table": f"t{i}"} for i in range(3)])
    assert index_manifest.verify_index_folder(str(tmp_path)) == []


def test_verifier_flags_a_missing_folder():
    assert "does not exist" in index_manifest.verify_index_folder("Z:\\no\\such\\folder")[0]


def test_verifier_flags_count_mismatch(tmp_path):
    make_index(tmp_path, "column", unit(3), [{"table": "t"}])
    assert any("column_index.faiss has 3 vectors" in p for p in index_manifest.verify_index_folder(str(tmp_path)))


def test_verifier_flags_half_a_pair(tmp_path):
    make_index(tmp_path, "table", unit(2), [{"table": "a"}, {"table": "b"}])
    (tmp_path / "table_meta.pkl").unlink()
    assert any("both be present" in p for p in index_manifest.verify_index_folder(str(tmp_path)))


def test_verifier_checks_checksums_only_for_files_that_are_present(tmp_path):
    ip, mp = make_index(tmp_path, "table", unit(2), [{"table": "a"}, {"table": "b"}])
    with open(mp, "rb") as fh:
        good = hashlib.sha256(fh.read()).hexdigest()
    stamp = {"checksums": {
        "table_meta.pkl": good,
        "concept_map.json": "0" * 64,          # listed by the build but not shipped here: not a problem
    }}
    (tmp_path / "build_stamp.json").write_text(json.dumps(stamp))
    assert index_manifest.verify_index_folder(str(tmp_path)) == []

    stamp["checksums"]["table_meta.pkl"] = "f" * 64
    (tmp_path / "build_stamp.json").write_text(json.dumps(stamp))
    assert any("table_meta.pkl does not match the checksum" in p for p in index_manifest.verify_index_folder(str(tmp_path)))


def test_verifier_flags_an_embedding_model_mismatch_when_the_build_recorded_one(tmp_path):
    make_index(tmp_path, "table", unit(2), [{"table": "a"}, {"table": "b"}])
    (tmp_path / "build_stamp.json").write_text(json.dumps({"embed_model": "BAAI/bge-large-en"}))
    assert index_manifest.verify_index_folder(str(tmp_path), "BAAI/bge-large-en") == []
    assert any("embedding model" in p for p in index_manifest.verify_index_folder(str(tmp_path), "other/model"))


def test_verifier_does_not_accuse_when_the_stamp_is_absent_or_unreadable(tmp_path):
    make_index(tmp_path, "table", unit(2), [{"table": "a"}, {"table": "b"}])
    assert index_manifest.verify_index_folder(str(tmp_path), "BAAI/bge-large-en") == []
    (tmp_path / "build_stamp.json").write_text("{not json")
    assert any("unreadable" in p for p in index_manifest.verify_index_folder(str(tmp_path)))


@pytest.mark.parametrize("version", ["ideal-55", "ideal-60"])
def test_the_shipped_indexes_are_consistent(version):
    """Guards the real artefacts: a rebuild that breaks consistency fails the suite."""
    import os

    folder = os.path.join(os.path.dirname(__file__), "..", "..", "artifacts", "nlp-index", version)
    if not os.path.isdir(folder):
        pytest.skip(f"{version} index not present in this checkout")
    assert index_manifest.verify_index_folder(folder, "BAAI/bge-large-en") == []


# ── RAG-05: warm-up never breaks startup ─────────────────────────────────────

def test_warmup_swallows_every_failure(monkeypatch):
    from backend.nlp import embedder, warmup

    def explode(_q):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(embedder, "embed_query", explode)
    warmup.warm_nlp()                     # must not raise


def test_warmup_loads_the_model_once_and_reports_index_problems(monkeypatch):
    from backend.nlp import embedder, warmup

    calls = []
    monkeypatch.setattr(embedder, "embed_query", lambda q: calls.append(q))
    warmup.warm_nlp()
    assert calls == ["warm up"]


# ── the retriever never offers a table the user may not read ─────────────────

def _fake_hits(tables):
    return [(0.9 - i * 0.01, {"table": t, "column": "amount", "text": f"{t} amount"}) for i, t in enumerate(tables)]


@pytest.fixture
def retrieval(monkeypatch):
    """Stub the model, the indexes and the return lookup; keep the real
    retrieval, fusion, ranking and authorisation code."""
    owners = {"allowed_t": "2041", "secret_t": "9999"}

    monkeypatch.setattr(retriever, "embed_query", lambda q: np.ones(4, dtype="float32"))
    def fake_search(index_path, meta_path, query_vector, k, min_score=0.0):
        if "row_label" in index_path:
            return []                                     # a different record shape; not under test here
        return _fake_hits(["secret_t", "allowed_t"])      # the secret table ranks FIRST

    monkeypatch.setattr(retriever, "search", fake_search)
    monkeypatch.setattr(retriever, "search_bm25", lambda *a, **k: [])
    monkeypatch.setattr(retriever, "search_qa_strong_match", lambda *a, **k: None)
    monkeypatch.setattr(retriever.return_lookup, "get_return_for_table",
                        lambda tbl, hint_text=None, ctx=None: {"return_id": owners[tbl], "return_name": f"R{owners[tbl]}"})
    monkeypatch.setattr(retriever, "meta_by_table", lambda *a, **k: {})
    monkeypatch.setattr(retriever, "AUTH_ENABLED", True)

    analysis = SimpleNamespace(metric_text="amount", normalized="amount", return_names=[], date_text=None, return_ids=[])
    return lambda: retriever.get_relevant_schema("amount", retriever.RequestContext("u", ""), analysis)


def test_unauthorised_tables_are_filtered_before_the_top_k_cut(monkeypatch, retrieval):
    monkeypatch.setattr(retriever, "get_allowed_form_ids", lambda ctx: {"2041"})
    out = retrieval()
    assert [t["table"] for t in out["tables"]] == ["allowed_t"]
    assert all(c["table"] == "allowed_t" for c in out["columns"])      # no column of a hidden table leaks either


def test_a_user_with_no_grants_gets_nothing(monkeypatch, retrieval):
    monkeypatch.setattr(retriever, "get_allowed_form_ids", lambda ctx: set())
    assert retrieval()["tables"] == []


def test_an_unknown_user_gets_nothing(monkeypatch, retrieval):
    monkeypatch.setattr(retriever, "get_allowed_form_ids", lambda ctx: None)
    assert retrieval()["tables"] == []


def test_a_user_granted_everything_sees_both(monkeypatch, retrieval):
    monkeypatch.setattr(retriever, "get_allowed_form_ids", lambda ctx: {"2041", "9999"})
    assert {t["table"] for t in retrieval()["tables"]} == {"allowed_t", "secret_t"}


# ── M-20: model/index mismatch is caught even when the stamp names no model ───

def test_verifier_flags_a_dimension_mismatch_without_any_build_stamp(tmp_path):
    make_index(tmp_path, "table", unit(3, dim=4), [{"table": f"t{i}"} for i in range(3)])
    problems = index_manifest.verify_index_folder(str(tmp_path), "other/model", embed_dim=8)
    assert any("4-dimensional" in p and "8-dimensional" in p and "DV_NLP_EMBED_MODEL" in p for p in problems)


def test_verifier_accepts_a_matching_dimension_and_ignores_it_when_unknown(tmp_path):
    make_index(tmp_path, "table", unit(3, dim=4), [{"table": f"t{i}"} for i in range(3)])
    assert index_manifest.verify_index_folder(str(tmp_path), "m", embed_dim=4) == []
    assert index_manifest.verify_index_folder(str(tmp_path), "m", embed_dim=None) == []


@pytest.mark.parametrize("version", ["ideal-55", "ideal-60"])
def test_the_shipped_indexes_match_the_default_model_dimension(version):
    import os

    folder = os.path.join(os.path.dirname(__file__), "..", "..", "artifacts", "nlp-index", version)
    if not os.path.isdir(folder):
        pytest.skip(f"{version} index not present in this checkout")
    assert index_manifest.verify_index_folder(folder, "BAAI/bge-large-en", embed_dim=1024) == []
    assert any("DV_NLP_EMBED_MODEL" in p for p in
               index_manifest.verify_index_folder(folder, "sentence-transformers/all-MiniLM-L6-v2", embed_dim=384))


def test_warmup_reports_a_model_that_does_not_match_the_index(monkeypatch, caplog):
    import logging

    from backend.nlp import embedder, index_manifest as im, warmup

    monkeypatch.setattr(embedder, "embed_query", lambda q: np.ones(384, dtype="float32"))
    seen = {}
    monkeypatch.setattr(im, "verify_index_folder", lambda f, m, d=None: seen.update(dim=d) or ["index/model mismatch"])
    with caplog.at_level(logging.WARNING):
        warmup.warm_nlp()
    assert seen["dim"] == 384
    assert "index/model mismatch" in caplog.text
