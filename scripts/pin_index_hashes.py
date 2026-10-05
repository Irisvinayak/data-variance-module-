"""Pin the embedding-index files so a swapped pickle is never loaded (code review M-07).

    python scripts/pin_index_hashes.py artifacts/nlp-index/ideal-60
    python scripts/pin_index_hashes.py artifacts/nlp-index/ideal-55 --check

Writes <folder>/pickle_manifest.json with the sha256 of every .pkl and .faiss file. From
then on the backend refuses to unpickle a file whose hash differs or that is not listed.
Run it again after each index rebuild (a rebuilt file no longer matches its old pin, which
is the point). --check only verifies and exits 1 on any mismatch, changing nothing.

Run it as the account that is allowed to write the folder, and keep every other account
read-only: the pin catches a swap, the folder permissions prevent one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

MANIFEST = "pickle_manifest.json"
PINNED_SUFFIXES = (".pkl", ".faiss")


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(folder: str) -> dict[str, str]:
    return {
        name: sha256_of(os.path.join(folder, name))
        for name in sorted(os.listdir(folder))
        if name.lower().endswith(PINNED_SUFFIXES) and os.path.isfile(os.path.join(folder, name))
    }


def check(folder: str) -> list[str]:
    path = os.path.join(folder, MANIFEST)
    if not os.path.isfile(path):
        return [f"{MANIFEST} does not exist in {folder}"]
    with open(path, encoding="utf-8") as fh:
        pinned = (json.load(fh) or {}).get("files") or {}
    problems = []
    current = build(folder)
    for name, digest in current.items():
        if name not in pinned:
            problems.append(f"{name} is not pinned")
        elif pinned[name] != digest:
            problems.append(f"{name} differs from its pin")
    for name in pinned:
        if name not in current:
            problems.append(f"{name} is pinned but missing")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pin or verify the index files' sha256.")
    parser.add_argument("folder")
    parser.add_argument("--check", action="store_true", help="verify only; exit 1 on a mismatch")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.folder):
        print(f"not a folder: {args.folder}", file=sys.stderr)
        return 2
    if args.check:
        problems = check(args.folder)
        for p in problems:
            print(f"PROBLEM: {p}")
        print("OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    files = build(args.folder)
    with open(os.path.join(args.folder, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump({"algorithm": "sha256", "files": files}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"pinned {len(files)} file(s) in {os.path.join(args.folder, MANIFEST)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
