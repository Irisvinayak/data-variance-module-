# error_contract.py - one shape for every error the API sends (code review M-24).
#
# Errors used to arrive in three styles: an HTTP error with {"detail": "..."}, a 422 with
# {"detail": [ ...list... ]}, and - on /variance/compute - a 200 whose body is
# {"error": "..."}. A client had to know which route it was calling to know how to read
# the failure. The contract is now ADDITIVE:
#
#   HTTP errors   {"detail": <unchanged>, "code": "<machine code>", "request_id": "<id>"}
#   /compute 200  the existing {"error": "..."} plus "error_code": "<machine code>"
#
# Nothing was removed or renamed, so a consumer that only reads `detail` / `error` keeps
# working. `code` is stable and meant for programs; `detail` stays human text and may be
# reworded. The status codes themselves are unchanged (changing 200-with-error or 500 into
# 404/422 is a separate, breaking step that the frontend has to adopt first).

from __future__ import annotations

from typing import Any

from ..data.calculate_variance import NO_DATA_PREFIX

_BY_STATUS = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}

TENANT_UNAVAILABLE = "tenant_unavailable"


def code_for(status_code: int, detail: Any = None) -> str:
    """The machine-readable code for an HTTP error."""
    if isinstance(detail, str) and detail.startswith(NO_DATA_PREFIX):
        return "no_data"
    from .errors import HOST_PROFILE_DETAIL    # lazy: errors.py is imported by the routes that import this module

    if detail == HOST_PROFILE_DETAIL:
        return TENANT_UNAVAILABLE
    if status_code in _BY_STATUS:
        return _BY_STATUS[status_code]
    return "server_error" if status_code >= 500 else "error"


def error_code_for_message(message: Any) -> str:
    """The code for a {"error": "..."} result returned with HTTP 200 by the service layer."""
    text = str(message or "")
    if text.startswith(NO_DATA_PREFIX):
        return "no_data"
    if text.startswith("Invalid"):
        return "invalid_input"
    if "could not be retrieved" in text:
        return "query_failed"
    if "Cannot build row identifier" in text:
        return "mapping_incomplete"
    return "error"


def error_body(status_code: int, detail: Any, request_id: str | None, code: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"detail": detail, "code": code or code_for(status_code, detail)}
    if request_id:
        body["request_id"] = request_id
    return body
