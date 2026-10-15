# index_manifest.py — verify an embedding-index folder against what built it.
#
# The index is produced by an external tool and dropped in wholesale; nothing
# used to check that the folder is complete, that its files are the ones the
# build recorded, or that the embedding model configured here is the one that
# made the vectors (code review RAG-01/RAG-02). The hard failures (a vector/
# record count mismatch, a wrong embedding size) are enforced at load/search time
# in index_store.py; this module is the broader, non-fatal report used by the
# warm-up log and the diagnostics endpoint.
#
# A problem here is a warning to the operator, never a reason to refuse to serve:
# the build stamp is optional and its absence is not an error.

from __future__ import annotations

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

_INDEX_PAIRS = (
    ("table_index.faiss", "table_meta.pkl"),
    ("column_index.faiss", "column_meta.pkl"),
    ("row_label_index.faiss", "row_label_meta.pkl"),
    ("qa_index.faiss", "qa_meta.pkl"),
)
_HASHES = {32: "md5", 40: "sha1", 64: "sha256"}


def _checksum_matches(path: str, expected: str) -> bool:
    algo = _HASHES.get(len(expected))
    if algo is None:
        return True                      # unknown format: cannot judge, do not accuse
    digest = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected.lower()


# ── Trust gate for pickle files (code review M-07) ───────────────────────────
# pickle.load runs code embedded in the file, so whoever can write the index folder can run
# code in this process. The index files are produced elsewhere and dropped in, so the app
# cannot tell a good file from a swapped one by itself. An operator can PIN the files:
#   python scripts/pin_index_hashes.py <index folder>
# writes pickle_manifest.json (sha256 per file). When that manifest exists, a pickle whose
# hash differs - or that the manifest does not list - is never loaded. Without a manifest
# behaviour is unchanged, unless DV_REQUIRE_INDEX_MANIFEST=true, which refuses every
# unpinned pickle. Folder permissions (write access for the build account only) are still
# the primary control; this catches a swap that gets past them.
PICKLE_MANIFEST_NAME = "pickle_manifest.json"
_trust_cache: dict = {}


def _require_manifest() -> bool:
    return os.environ.get("DV_REQUIRE_INDEX_MANIFEST", "").strip().lower() in {"1", "true", "yes", "on"}


def pickle_is_trusted(path: str) -> tuple[bool, str]:
    """(trusted, reason). Cheap on repeat calls: cached per (file, mtime, size)."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return False, f"cannot stat {os.path.basename(path)}: {exc.__class__.__name__}"
    key = (path, st.st_mtime_ns, st.st_size, _require_manifest())
    cached = _trust_cache.get(key)
    if cached is not None:
        return cached
    result = _check_pickle(path)
    if len(_trust_cache) > 256:
        _trust_cache.clear()
    _trust_cache[key] = result
    return result


def _check_pickle(path: str) -> tuple[bool, str]:
    name = os.path.basename(path)
    manifest_path = os.path.join(os.path.dirname(path), PICKLE_MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        if _require_manifest():
            return False, f"{PICKLE_MANIFEST_NAME} is missing and DV_REQUIRE_INDEX_MANIFEST=true"
        return True, "no manifest"
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            files = (json.load(fh) or {}).get("files") or {}
    except (OSError, ValueError):
        return False, f"{PICKLE_MANIFEST_NAME} is unreadable"
    expected = files.get(name)
    if not isinstance(expected, str) or len(expected) != 64:
        return False, f"{name} is not listed in {PICKLE_MANIFEST_NAME}"
    if not _checksum_matches(path, expected):
        return False, f"{name} does not match its pinned sha256"
    return True, "pinned"


def verify_index_folder(folder: str, embed_model: str = "", embed_dim: int | None = None) -> list[str]:
    """Human-readable problems with `folder`, or [] when it looks sound.

    `embed_dim` is the size of the vectors the CONFIGURED model actually produces
    (measured by the caller). The shipped build stamps record no model name, so the
    name comparison above never fires for them; comparing each index's own vector
    size with the live model catches the same mistake (code review M-20)."""
    problems: list[str] = []
    if not os.path.isdir(folder):
        return [f"index folder does not exist: {folder}"]

    stamp_path = os.path.join(folder, "build_stamp.json")
    if os.path.isfile(stamp_path):
        try:
            with open(stamp_path, encoding="utf-8") as fh:
                stamp = json.load(fh)
        except (OSError, ValueError) as exc:
            problems.append(f"build_stamp.json is unreadable: {exc}")
            stamp = {}
        # Only files that are present are judged: the stamp may list artefacts
        # (concept maps, reports) that this application never loads and that a
        # deployment legitimately omits.
        for name, expected in (stamp.get("checksums") or {}).items():
            path = os.path.join(folder, name)
            if isinstance(expected, str) and os.path.isfile(path) and not _checksum_matches(path, expected):
                problems.append(f"{name} does not match the checksum recorded by the build")
        recorded = str(stamp.get("embed_model") or stamp.get("embedding_model") or "").strip()
        if recorded and embed_model and recorded != embed_model:
            problems.append(
                f"the build used embedding model {recorded!r} but DV_NLP_EMBED_MODEL is {embed_model!r}"
            )
        stamped_dim = stamp.get("embed_dim")
        if isinstance(stamped_dim, int) and embed_dim is not None and stamped_dim != embed_dim:
            problems.append(
                f"the build stamp records {stamped_dim}-dimensional vectors but the configured model "
                f"{embed_model!r} produces {embed_dim}-dimensional ones"
            )

    try:
        import faiss
        import pickle
    except Exception as exc:  # noqa: BLE001 - optional dependency, only needed for this check
        return problems + [f"vector consistency not checked ({type(exc).__name__}: {exc})"]

    for index_name, meta_name in _INDEX_PAIRS:
        ip, mp = os.path.join(folder, index_name), os.path.join(folder, meta_name)
        if os.path.isfile(ip) != os.path.isfile(mp):
            problems.append(f"{index_name} and {meta_name} must both be present or both absent")
            continue
        if not os.path.isfile(ip):
            continue
        try:
            index = faiss.read_index(ip)
            with open(mp, "rb") as fh:
                meta = pickle.load(fh)  # trusted, operator-supplied artefact (see review M-07)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{index_name} could not be read: {type(exc).__name__}: {exc}")
            continue
        if index.ntotal != len(meta):
            problems.append(f"{index_name} has {index.ntotal} vectors but {meta_name} has {len(meta)} records")
        if embed_dim is not None and index.d != embed_dim:
            problems.append(
                f"{index_name} holds {index.d}-dimensional vectors but the configured embedding model "
                f"{embed_model!r} produces {embed_dim}-dimensional ones; DV_NLP_EMBED_MODEL does not match the index"
            )
    return problems
