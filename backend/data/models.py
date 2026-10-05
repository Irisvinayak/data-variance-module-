# models.py — Pydantic request/response models.
#
# Limits here (code review API-01 / API-02) are set well above anything the UI
# sends, so they refuse abuse without touching normal use. What the frontend
# actually sends: comparison_mode is "vs_current" or "sequential"; reporting_date
# and comparison_dates come from /variance/dates as DD-MON-YYYY; reporting_period
# is 1-3; the NL box is a sentence.

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_DATE_PATTERN = r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"
_MAX_CONTEXT_BYTES = 8 * 1024


class VarianceComputeRequest(BaseModel):
    return_id:          str = Field(max_length=64)
    # Optional now (it was required but is ignored): the server resolves the
    # mapping from the return's own TblPath (see service._trusted_tbl_path), so a
    # client that omits it works, and one that still sends it is unaffected.
    table_mapping_path: str = Field(default="", max_length=1024)
    table_name:         str = Field(max_length=256)
    reporting_date:     str = Field(pattern=_DATE_PATTERN, max_length=16)
    # ge=1: 0 or a negative count used to be silently coerced to 1. The upper
    # bound the API enforces (MAX_REPORTING_PERIOD) still answers 400 with its
    # own message; this ceiling only stops absurd values at parse time.
    reporting_period:   int = Field(default=1, ge=1, le=100)
    selected_columns:   Optional[list[str]] = Field(default=None, max_length=200)
    # Unknown values used to be silently treated as "vs_current".
    comparison_mode:    Literal["vs_current", "sequential"] = "vs_current"
    # Exact reporting dates to compare, "DD-MON-YYYY", newest treated as the
    # current period. When present it REPLACES reporting_period's derived
    # comparison periods (the manual UI's date checkboxes — ControlBar's
    # DateField); reporting_period is then ignored. Capped at
    # MAX_COMPARISON_DATES in backend/api/variance.py, matching the Periods chips' own 1-3
    # range, because the result table renders one column group per period.
    comparison_dates:   Optional[list[str]] = Field(default=None, max_length=20)

    @field_validator("selected_columns")
    @classmethod
    def _column_length(cls, v):
        if v and any(len(c) > 128 for c in v):
            raise ValueError("column name too long")
        return v

    @field_validator("comparison_dates")
    @classmethod
    def _date_length(cls, v):
        if v and any(len(d) > 16 for d in v):
            raise ValueError("date too long")
        return v


class NLResolveRequest(BaseModel):
    query: str = Field(max_length=1000)
    # All optional/additive — present only on a clarification follow-up
    # request. `dimension` says which prior clarification is being answered
    # ("return" or "table" — see the needs_clarification response's own
    # `dimension` field), `clarification_answer` is the `id` of the option
    # the user picked (or the "__skip__" sentinel — see backend/main.py),
    # and `resolved_context` is echoed straight back from whatever the prior
    # needs_clarification response sent, so no server-side session store is
    # needed between requests.
    dimension:            Optional[Literal["return", "table"]] = None
    clarification_answer: Optional[str] = Field(default=None, max_length=256)
    resolved_context:     Optional[dict[str, Any]] = None

    @field_validator("resolved_context")
    @classmethod
    def _bounded_context(cls, v):
        # Echoed back verbatim, so it is client-controlled input of any size.
        if v is not None and len(json.dumps(v, default=str)) > _MAX_CONTEXT_BYTES:
            raise ValueError("resolved_context too large")
        return v
