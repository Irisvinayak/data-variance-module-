# models.py — Pydantic request/response models.

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class VarianceComputeRequest(BaseModel):
    return_id:          str
    table_mapping_path: str
    table_name:         str
    reporting_date:     str
    reporting_period:   int = 1
    selected_columns:   Optional[List[str]] = None
    comparison_mode:    str = "vs_current"


class NLResolveRequest(BaseModel):
    query: str
    # All optional/additive — present only on a clarification follow-up
    # request. `dimension` says which prior clarification is being answered
    # ("return" or "table" — see the needs_clarification response's own
    # `dimension` field), `clarification_answer` is the `id` of the option
    # the user picked (or the "__skip__" sentinel — see backend/main.py),
    # and `resolved_context` is echoed straight back from whatever the prior
    # needs_clarification response sent, so no server-side session store is
    # needed between requests.
    dimension:            Optional[str] = None
    clarification_answer: Optional[str] = None
    resolved_context:     Optional[Dict[str, Any]] = None
