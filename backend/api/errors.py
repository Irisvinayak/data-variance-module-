# errors.py — the shared HTTP error mapping for the route modules.
#
# What the client sees is deliberately LESS than what the log records (code
# review SEC-04): exception text can carry filesystem paths, schema-qualified
# table names, Oracle error text and tenant lists. The full detail is logged
# with the request id; the client gets a fixed message plus that id, so a user
# can quote it and an operator can find the line.
#
#   HTTPException     -> passed through unchanged (a deliberate, already-safe 4xx)
#   InvalidInput      -> 400  "Invalid ..." (never echoes the value)
#   FileNotFoundError -> 404  generic
#   KeyError          -> 404  generic
#   HostProfileError  -> 400  generic
#   RuntimeError      -> 500  generic + request id
#   anything else     -> 500  generic + request id
#
# Call it from a single `except Exception` clause around the guarded block.

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from ..data.validation import InvalidInput
from ..hosts import HostProfileError
from ..logging_config import request_id_var

logger = logging.getLogger(__name__)

NOT_FOUND_DETAIL = "The requested data was not found."
HOST_PROFILE_DETAIL = "This request cannot be served for the current tenant or configuration."


def server_error_detail(request_id: str | None = None) -> str:
    """The fixed 500 body, carrying the request id for correlation."""
    rid = request_id or request_id_var.get() or "-"
    return f"The server could not complete the request. Reference: {rid}"


def http_error(exc: Exception, route: str, ctx=None) -> HTTPException:
    """Map a domain exception to the HTTPException a route should raise.

    `route` and `ctx` only shape the log line; they never affect the response.
    Usage:  except Exception as exc: raise http_error(exc, "/x", ctx) from exc
    """
    # A deliberate 4xx raised inside the guarded block (e.g. an access check)
    # already says what the client should see; re-wrapping it as a generic
    # 500 would hide it.
    if isinstance(exc, HTTPException):
        return exc

    where = f"{route} | {ctx}" if ctx is not None else route

    if isinstance(exc, InvalidInput):
        logger.warning("[api] 400 InvalidInput | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if isinstance(exc, FileNotFoundError):
        logger.error("[api] 404 FileNotFoundError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)

    if isinstance(exc, KeyError):
        logger.error("[api] 404 KeyError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)

    # HostProfileError subclasses RuntimeError, so it MUST be tested before the
    # RuntimeError branch below. Its text is written for an operator (it can
    # list every tenant), so it is logged, never returned.
    if isinstance(exc, HostProfileError):
        logger.warning("[api] 400 HostProfileError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=HOST_PROFILE_DETAIL)

    if isinstance(exc, RuntimeError):
        logger.error("[api] 500 RuntimeError | %s | %s", where, exc)
        return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=server_error_detail())

    logger.exception("[api] 500 Unhandled exception | %s", where)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=server_error_detail())
