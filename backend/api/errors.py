# errors.py — the shared HTTP error mapping for the route modules.
#
# This exact five-branch except-ladder was written out three times in main.py
# (variance_compute, variance_dates, variance_nlresolve), including the same
# six-line explanatory comment twice over. The mapping was identical in all
# three copies; only the log text differed.
#
# The mapping is reproduced EXACTLY as it was, including the detail strings,
# because the frontend renders `detail` verbatim:
#
#   FileNotFoundError -> 404  str(exc)
#   KeyError          -> 404  str(exc)   (note: str() of a KeyError is quoted)
#   HostProfileError  -> 400  str(exc)
#   RuntimeError      -> 500  str(exc)
#   anything else     -> 500  "Unexpected server error: <Type>: <exc>"
#
# Call it from a single `except Exception` clause, which is what the last rung
# of every original ladder was — so an HTTPException raised inside a guarded
# block still lands on the generic-500 rung exactly as it did before. That
# shadowing may well be wrong, but changing it is a behaviour change and does
# not belong in a refactor.

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from ..hosts import HostProfileError

logger = logging.getLogger(__name__)


def http_error(exc: Exception, route: str, ctx=None) -> HTTPException:
    """Map a domain exception to the HTTPException the routes used to raise.

    `route` and `ctx` only shape the log line; they never affect the response.
    Usage:  except Exception as exc: raise http_error(exc, "/x", ctx) from exc
    """
    where = f"{route} | {ctx}" if ctx is not None else route

    if isinstance(exc, FileNotFoundError):
        logger.error("[api] 404 FileNotFoundError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if isinstance(exc, KeyError):
        logger.error("[api] 404 KeyError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    # HostProfileError subclasses RuntimeError, so it MUST be tested before the
    # RuntimeError branch below. Otherwise a configuration/authorisation fault
    # (unknown tenant, tenant not provisioned) is reported as a generic 500,
    # hiding the operator-actionable message inside a scary wrapper.
    if isinstance(exc, HostProfileError):
        logger.warning("[api] 400 HostProfileError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if isinstance(exc, RuntimeError):
        logger.error("[api] 500 RuntimeError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    logger.exception("[api] 500 Unhandled exception | %s", where)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unexpected server error: {type(exc).__name__}: {exc}",
    )
