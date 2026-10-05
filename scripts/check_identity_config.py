"""Pre-flight check for the C-01 identity rollout. Run it ON the server, with that
server's real environment:

    python scripts/check_identity_config.py            # report
    python scripts/check_identity_config.py --require  # exit 1 unless ready for 'required'

It reads the same settings the backend reads and tells you what is missing before
you flip DV_IDENTITY_MODE. It NEVER prints the secret: only whether it is set and
how long it is. Exit code 0 = no problems found, 1 = problems.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MIN_SECRET_BYTES = 32          # the 6.0 API refuses to start below this too
KNOWN_PLACEHOLDERS = {"https://localhost:7072"}   # the repository's ValidAudience placeholder (HX-07)


def check(cfg, require: bool = False) -> list[str]:
    """Problems with the identity settings in `cfg` (a settings module)."""
    problems: list[str] = []
    mode = cfg.IDENTITY_MODE
    if cfg.IDENTITY_MODE_INVALID:
        problems.append("DV_IDENTITY_MODE is not one of off / optional / required (treated as off).")
    if require and mode != "required":
        problems.append(f"DV_IDENTITY_MODE is '{mode}', not 'required'.")
    if mode != "off" or require:
        secret = cfg.JWT_SECRET or ""
        if not secret:
            problems.append("DV_JWT_SECRET is not set: every token would be refused.")
        elif len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
            problems.append(f"DV_JWT_SECRET is shorter than {MIN_SECRET_BYTES} bytes; use a generated key.")
        if not cfg.JWT_ISSUER:
            problems.append("DV_JWT_ISSUER is empty: the issuer is not checked (copy ValidIssuer from the API).")
        if not cfg.JWT_AUDIENCE:
            problems.append("DV_JWT_AUDIENCE is empty: the audience is not checked (copy ValidAudience from the API).")
        elif cfg.JWT_AUDIENCE in KNOWN_PLACEHOLDERS:
            problems.append("DV_JWT_AUDIENCE is the repository placeholder; use the deployed ValidAudience.")
    if not cfg.AUTH_ENABLED:
        problems.append("DV_AUTH_ENABLED=false: authorisation is off (identity checks alone do not restrict access).")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--require", action="store_true", help="check readiness for DV_IDENTITY_MODE=required")
    args = parser.parse_args(argv)

    from backend.config import settings as cfg

    print(f"VERSION={cfg.APP_VERSION}  DV_IDENTITY_MODE={cfg.IDENTITY_MODE}  DV_AUTH_ENABLED={cfg.AUTH_ENABLED}")
    print(f"DV_JWT_SECRET: {'set' if cfg.JWT_SECRET else 'NOT SET'}"
          + (f" ({len(cfg.JWT_SECRET.encode('utf-8'))} bytes)" if cfg.JWT_SECRET else ""))
    print(f"DV_JWT_ISSUER: {'set' if cfg.JWT_ISSUER else 'NOT SET'}   DV_JWT_AUDIENCE: {'set' if cfg.JWT_AUDIENCE else 'NOT SET'}")
    problems = check(cfg, require=args.require)
    for p in problems:
        print(f"PROBLEM: {p}")
    print("OK" if not problems else f"{len(problems)} problem(s) found")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
