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
