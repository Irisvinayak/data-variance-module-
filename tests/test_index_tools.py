"""M-20 (stamp the embedding model), M-28 (lock file tool) and M-07 (pin tool) - the operator scripts.

Temp folders only. No production index is read or written, and nothing is installed.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import pickle

import faiss
import numpy as np
import pytest

from backend.nlp import index_manifest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stamp = _tool("stamp_embed_model")
lock = _tool("make_lock")
pin = _tool("pin_index_hashes")


def make_index(folder, name, dim, n=3, meta_name=None):
    index = faiss.IndexFlatIP(dim)
    index.add(np.ones((n, dim), dtype="float32"))
    faiss.write_index(index, str(folder / name))
    if meta_name:
        (folder / meta_name).write_bytes(pickle.dumps([{"table": f"t{i}"} for i in range(n)]))


# ── M-20: stamp_embed_model ──────────────────────────────────────────────────

def test_the_vector_size_is_read_from_the_index_not_guessed(tmp_path):
    make_index(tmp_path, "table_index.faiss", 8)
    make_index(tmp_path, "column_index.faiss", 8)
    out, problems = stamp.plan(str(tmp_path), "my/model")
    assert problems == [] and out == {"embed_model": "my/model", "embed_dim": 8}


def test_an_existing_stamp_keeps_every_other_key(tmp_path):
    make_index(tmp_path, "table_index.faiss", 4)
    original = {"published_at": "2026-09-15", "label": "QCB", "tables": 10, "files": ["a.pkl"], "returns": ["x"]}
    (tmp_path / "build_stamp.json").write_text(json.dumps(original), encoding="utf-8")
    out, problems = stamp.plan(str(tmp_path), "m")
    assert problems == [] and {k: out[k] for k in original} == original
    assert out["embed_model"] == "m" and out["embed_dim"] == 4


def test_without_write_nothing_changes_and_with_write_the_file_is_updated(tmp_path, capsys):
    make_index(tmp_path, "table_index.faiss", 4)
    path = tmp_path / "build_stamp.json"
    path.write_text('{"label": "x"}', encoding="utf-8")
    assert stamp.main([str(tmp_path), "--model", "m"]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"label": "x"}
    assert "not written" in capsys.readouterr().out
    assert stamp.main([str(tmp_path), "--model", "m", "--write"]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"label": "x", "embed_model": "m", "embed_dim": 4}
    assert not (tmp_path / "build_stamp.json.tmp").exists()


def test_a_folder_without_a_stamp_gets_a_minimal_one(tmp_path):
    make_index(tmp_path, "table_index.faiss", 4)
    assert stamp.main([str(tmp_path), "--model", "m", "--write"]) == 0
    assert json.loads((tmp_path / "build_stamp.json").read_text(encoding="utf-8")) == {"embed_model": "m", "embed_dim": 4}


@pytest.mark.parametrize("setup,needle", [
    (lambda p: (make_index(p, "table_index.faiss", 4), make_index(p, "column_index.faiss", 8)), "disagree"),
    (lambda p: None, "no index files"),
    (lambda p: (make_index(p, "table_index.faiss", 4),
                (p / "build_stamp.json").write_text('{"embed_model": "other/model"}', encoding="utf-8")), "already names"),
    (lambda p: (make_index(p, "table_index.faiss", 4),
                (p / "build_stamp.json").write_text('{"embed_dim": 99}', encoding="utf-8")), "99"),
    (lambda p: (make_index(p, "table_index.faiss", 4), (p / "build_stamp.json").write_text("{broken", encoding="utf-8")), "unreadable"),
])
def test_it_refuses_rather_than_overwrite_or_guess(tmp_path, setup, needle):
    setup(tmp_path)
    before = (tmp_path / "build_stamp.json").read_bytes() if (tmp_path / "build_stamp.json").exists() else None
    assert stamp.main([str(tmp_path), "--model", "m", "--write"]) == 1
    after = (tmp_path / "build_stamp.json").read_bytes() if (tmp_path / "build_stamp.json").exists() else None
    assert before == after                                  # nothing written
    _, problems = stamp.plan(str(tmp_path), "m")
    assert any(needle in p for p in problems)


def test_a_missing_folder_is_an_error(tmp_path):
    assert stamp.main([str(tmp_path / "nope")]) == 2


def test_the_default_model_is_the_configured_one(tmp_path):
    from backend.nlp import nlp_config

    make_index(tmp_path, "table_index.faiss", 4)
    assert stamp.plan(str(tmp_path))[0]["embed_model"] == nlp_config.EMBED_MODEL


# ── the backend uses what the stamp records ──────────────────────────────────

def test_a_stamped_vector_size_that_the_model_does_not_match_is_reported(tmp_path):
    make_index(tmp_path, "table_index.faiss", 8, meta_name="table_meta.pkl")
    stamp.main([str(tmp_path), "--model", "builder/model", "--write"])
    problems = index_manifest.verify_index_folder(str(tmp_path), "other/model", embed_dim=16)
    assert any("builder/model" in p or "'builder/model'" in p for p in problems)          # the model name differs
    assert any("8-dimensional" in p and "16-dimensional" in p for p in problems)


def test_a_matching_stamp_reports_nothing(tmp_path):
    make_index(tmp_path, "table_index.faiss", 8, meta_name="table_meta.pkl")
    stamp.main([str(tmp_path), "--model", "builder/model", "--write"])
    assert index_manifest.verify_index_folder(str(tmp_path), "builder/model", embed_dim=8) == []


def test_the_stamp_check_is_skipped_when_the_dimension_is_unknown(tmp_path):
    make_index(tmp_path, "table_index.faiss", 8, meta_name="table_meta.pkl")
    stamp.main([str(tmp_path), "--model", "m", "--write"])
    assert index_manifest.verify_index_folder(str(tmp_path), "m") == []


# ── M-28: make_lock ──────────────────────────────────────────────────────────

def test_the_requirement_names_are_parsed_from_the_real_file():
    names = lock.read_requirement_names()
    assert {"fastapi", "uvicorn", "oracledb", "sqlglot", "defusedxml", "python-dotenv"} <= set(names)
    assert "#" not in "".join(names)


def test_requirement_parsing_handles_comments_extras_and_blank_lines(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("# header\n\nfastapi>=0.110  # web\nuvicorn[standard]>=0.29\nSome_Pkg.name==1\n-r other.txt\n", encoding="utf-8")
    assert lock.read_requirement_names(str(f)) == ["fastapi", "uvicorn", "some-pkg-name"]


def test_the_closure_follows_dependencies_and_reports_installed_versions():
    pins = lock.closure(["fastapi"])
    assert "fastapi" in pins and "starlette" in pins and "pydantic" in pins
    from importlib import metadata

    assert pins["starlette"] == metadata.version("starlette")


def test_a_missing_root_is_an_error_not_a_silent_omission():
    with pytest.raises(SystemExit, match="not installed"):
        lock.closure(["definitely-not-installed-xyz"])


def test_render_and_compare_ignore_the_header():
    text = lock.render({"a": "1.0", "b": "2.0"})
    assert text.startswith("# requirements.lock") and "a==1.0\nb==2.0\n" in text
    assert lock.body_of(text) == ["a==1.0", "b==2.0"]


def test_the_committed_lock_covers_every_requirement_and_matches_this_environment():
    path = ROOT / "requirements.lock"
    assert path.exists()
    body = lock.body_of(path.read_text(encoding="utf-8"))
    locked = {line.split("==")[0] for line in body}
    assert set(lock.read_requirement_names()) <= locked
    assert all("==" in line for line in body)
    assert lock.main(["--check"]) == 0                      # not stale against the tested environment


def test_check_reports_a_stale_lock(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "requirements.lock"
    stale.write_text("# x\nfastapi==0.0.1\n", encoding="utf-8")
    monkeypatch.setattr(lock, "LOCK", str(stale))
    assert lock.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().out
    monkeypatch.setattr(lock, "LOCK", str(tmp_path / "absent.lock"))
    assert lock.main(["--check"]) == 1


def test_writing_a_lock_changes_no_installed_package(tmp_path, monkeypatch):
    target = tmp_path / "requirements.lock"
    monkeypatch.setattr(lock, "LOCK", str(target))
    assert lock.main([]) == 0 and target.exists()
    assert lock.main(["--check"]) == 0


def test_the_lock_pins_the_versions_that_fix_the_reported_vulnerabilities():
    from packaging.version import Version

    pins = dict(line.split("==") for line in lock.body_of((ROOT / "requirements.lock").read_text(encoding="utf-8")))
    for name, minimum in {"starlette": "1.3.1", "anyio": "4.14.2", "cryptography": "50.0.0", "urllib3": "2.8.0"}.items():
        assert Version(pins[name]) >= Version(minimum), name


# ── M-07: the pin tool is safe ───────────────────────────────────────────────

def test_pinning_never_modifies_the_files_it_pins(tmp_path):
    (tmp_path / "a.pkl").write_bytes(b"content")
    before = (tmp_path / "a.pkl").read_bytes()
    pin.main([str(tmp_path)])
    assert (tmp_path / "a.pkl").read_bytes() == before


def test_pinning_ignores_other_files_and_subfolders(tmp_path):
    (tmp_path / "a.pkl").write_bytes(b"1")
    (tmp_path / "notes.txt").write_bytes(b"2")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.pkl").write_bytes(b"3")
    pin.main([str(tmp_path)])
    assert set(json.loads((tmp_path / "pickle_manifest.json").read_text(encoding="utf-8"))["files"]) == {"a.pkl"}


def test_a_pinned_folder_still_loads_through_the_backend_gate(tmp_path):
    (tmp_path / "table_meta.pkl").write_bytes(pickle.dumps([1]))
    pin.main([str(tmp_path)])
    index_manifest._trust_cache.clear()
    assert index_manifest.pickle_is_trusted(str(tmp_path / "table_meta.pkl")) == (True, "pinned")
