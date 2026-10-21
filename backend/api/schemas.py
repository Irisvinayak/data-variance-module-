# schemas.py - response models, so the OpenAPI schema describes what the routes return
# (code review M-42). They DOCUMENT the existing responses; they do not change them:
#
#   * every field is optional, because /compute reports failure as {"error": ...} with
#     the same status, and the NL route adds fields of its own;
#   * extra keys are allowed and kept, so nothing a route returns is dropped;
#   * routes use response_model_exclude_unset=True, so a key the route did not return is
#     never added as `null`.
#
# The shapes mirror docs/models.md.

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class HealthResponse(_Loose):
    status: Optional[str] = None
    version: Optional[str] = None
    profile: Optional[str] = None
    paths: Optional[Any] = None


class AppConfigResponse(_Loose):
    version: Optional[str] = None
    profile: Optional[str] = None
    requires_tenant: Optional[bool] = None


class MyReturnsResponse(_Loose):
    login_id: Optional[str] = None
    tenant_id: Optional[str] = None
    allowed_count: Optional[int] = None
    allowed_forms: Optional[list[str]] = None


class FindResponse(_Loose):
    """One return (return_id, tables, ...) or a candidate list (candidates)."""

    return_id: Optional[str] = None
    return_name: Optional[str] = None
    report_freq: Optional[str] = None
    table_mapping_path: Optional[str] = None
    tables: Optional[list[dict[str, Any]]] = None
    candidates: Optional[list[dict[str, Any]]] = None
    score: Optional[Any] = None


class DatesResponse(_Loose):
    dates: Optional[list[str]] = None


class ComputeResponse(_Loose):
    table_name: Optional[str] = None
    reporting_date: Optional[str] = None
    comparison_periods: Optional[list[str]] = None
    columns: Optional[list[str]] = None
    display_columns: Optional[list[str]] = None
    rows: Optional[list[dict[str, Any]]] = None
    comparison_mode: Optional[str] = None
    chain_dates: Optional[list[str]] = None
    missing_periods: Optional[list[str]] = None
    truncated: Optional[bool] = None
    truncation_notice: Optional[str] = None
    duplicate_identifiers: Optional[list[str]] = None
    duplicate_notice: Optional[str] = None
    removed_rows: Optional[list[dict[str, Any]]] = None
    removed_notice: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


class ClarificationFields(_Loose):
    """A "needs more detail" answer (200) instead of a result: the NL layer could not decide."""

    needs_clarification: Optional[bool] = None
    dimension: Optional[str] = None                 # "return" | "table" | "date"
    question: Optional[str] = None
    options: Optional[list[dict[str, Any]]] = None  # [{"id", "label"}]
    skippable: Optional[bool] = None
    allow_other: Optional[bool] = None
    confidence: Optional[float] = None
    resolved_context: Optional[dict[str, Any]] = None
    interpretation: Optional[dict[str, Any]] = None


class NlResolveResponse(ComputeResponse, ClarificationFields):
    """/variance/nlresolve: either a clarification, or a compute result (all ComputeResponse
    fields) plus the return it was resolved to."""

    return_id: Optional[str] = None
    return_name: Optional[str] = None
    report_freq: Optional[str] = None
    table_mapping_path: Optional[str] = None
    resolution_mode: Optional[str] = None           # "llm" | "fallback" | "qa"


class NlQueryResponse(ClarificationFields):
    """/variance/nlquery (disabled by default): raw result columns/rows, or a clarification."""

    columns: Optional[list[str]] = None
    rows: Optional[list[Any]] = None
    sql: Optional[str] = None                       # only when DV_NLQUERY_RETURN_SQL=true
