"""Record which embedding model built an index, in its build_stamp.json (code review M-20).

    python scripts/stamp_embed_model.py artifacts/nlp-index/ideal-60                 # show what would be written
    python scripts/stamp_embed_model.py artifacts/nlp-index/ideal-60 --write         # write it
    python scripts/stamp_embed_model.py <folder> --write --model BAAI/bge-large-en   # name it explicitly

The index builder is an external tool that this repository does not contain, and the stamps it
ships record no model, so the backend could only notice a model/index mismatch from the vector
size. Run this once per index folder, by the person who knows which model built it. It adds
two keys and touches nothing else:

    "embed_model": the model name (default: the configured DV_NLP_EMBED_MODEL)
    "embed_dim":   the vector size, READ FROM THE INDEX FILES (never guessed)

An existing build_stamp.json keeps every other key exactly as it was; a folder without one gets a
minimal stamp. If an index files disagree on the vector size, or the stamp already names a
different model, it stops and says so instead of overwriting. Without --write nothing is changed.
The backend then compares both values at start-up (nlp/index_manifest.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STAMP = "build_stamp.json"
INDEX_FILES = ("table_index.faiss", "column_index.faiss", "row_label_index.faiss", "qa_index.faiss")


def read_dimensions(folder: str) -> dict[str, int]:
    import faiss

    dims = {}
    for name in INDEX_FILES:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            dims[name] = int(faiss.read_index(path).d)
    return dims


def plan(folder: str, model: str | None = None) -> tuple[dict, list[str]]:
    """(the stamp as it would be written, problems that forbid writing it)."""
    problems: list[str] = []
    path = os.path.join(folder, STAMP)
    stamp: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                stamp = json.load(fh)
        except (OSError, ValueError) as exc:
            return {}, [f"{STAMP} is unreadable: {exc}"]
        if not isinstance(stamp, dict):
            return {}, [f"{STAMP} is not a JSON object"]

    dims = read_dimensions(folder)
    if not dims:
        problems.append("no index files found, so the vector size cannot be read")
    elif len(set(dims.values())) != 1:
        problems.append(f"the index files disagree on the vector size: {dims}")

    if model is None:
        from backend.nlp import nlp_config

        model = nlp_config.EMBED_MODEL
    existing = str(stamp.get("embed_model") or stamp.get("embedding_model") or "").strip()
    if existing and existing != model:
        problems.append(f"the stamp already names model {existing!r}, not {model!r}; fix that by hand if it is wrong")

    out = dict(stamp)
    out["embed_model"] = model
    if dims and len(set(dims.values())) == 1:
        previous_dim = stamp.get("embed_dim")
        dim = next(iter(dims.values()))
        if previous_dim is not None and previous_dim != dim:
            problems.append(f"the stamp says embed_dim {previous_dim} but the index files are {dim}-dimensional")
        out["embed_dim"] = dim
    return out, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add embed_model / embed_dim to an index folder's build_stamp.json.")
    parser.add_argument("folder")
    parser.add_argument("--model", help="the embedding model name (default: DV_NLP_EMBED_MODEL)")
    parser.add_argument("--write", action="store_true", help="write the stamp (otherwise only show it)")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.folder):
        print(f"not a folder: {args.folder}", file=sys.stderr)
        return 2
    stamp, problems = plan(args.folder, args.model)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        return 1
    print(f"embed_model = {stamp['embed_model']}")
    print(f"embed_dim   = {stamp['embed_dim']}")
    if not args.write:
        print("(not written - pass --write)")
        return 0
    path = os.path.join(args.folder, STAMP)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(stamp, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
