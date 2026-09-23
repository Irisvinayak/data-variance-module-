# Data models and API contracts

Written for developers working on this repository.

Every payload below was captured from a live server (iDEAL 6.0, tenant 1001)
rather than written by hand.

> **Responses are untyped.** No route declares a FastAPI `response_model`, so
> `/openapi.json` documents the request bodies but **nothing** about responses.
> This file is the contract until that changes. If you add a route, consider
> declaring a response model so the schema is generated rather than documented.

## Internal models

### `RequestContext` — [backend/config/context.py](../backend/config/context.py)

Frozen dataclass; *who is asking, and which tenant's repository answers them*.

| Field | Type | Notes |
|---|---|---|
| `login_id` | `str` | 5.5: a username (`iris810`). 6.0: an **email address**. Never parsed — only matched against the user master. |
| `tenant_id` | `str` | `""` under 5.5 (no tenancy). Required and validated against `XML_Tenant.xml` under 6.0. |

Both are stripped in `__post_init__`, so `" 1001"` cannot become a second cache
entry for tenant `1001`. `cache_key` is `(tenant_id, login_id)` in both modes —
harmless under 5.5, essential under 6.0, and one cache shape for both.

`ANONYMOUS` is the module-level instance for code with no request identity.

### `HostProfile` — [backend/hosts/base.py](../backend/hosts/base.py)

Not a data model but the other core abstraction. Path methods (`returns_xml_path`,
`db_dir`, `user_xml_path`, …) all take a `RequestContext`; shape properties
(`returns_row_tag`, `dept_forms_attr`, `forms_delimiter`, `period_freq_attr`, …)
describe the file format. `describe(ctx)` returns every resolved path and powers
`/health`.

## Request models

Both are Pydantic models in [backend/data/models.py](../backend/data/models.py).
Everything else arrives as query parameters.

### `VarianceComputeRequest`

| Field | Type | Required | Notes |
|---|---|---|---|
| `return_id` | `str` | yes | |
| `table_mapping_path` | `str` | yes | Absolute path from `/variance/find` |
| `table_name` | `str` | yes | Logical name; the schema and `_DP` suffix are added server-side |
| `reporting_date` | `str` | yes | `DD-MON-YYYY`, e.g. `05-AUG-2026` |
| `reporting_period` | `int` | no (1) | How many prior periods to compare |
| `selected_columns` | `list[str] \| None` | no | Restricts the result columns |
| `comparison_mode` | `str` | no (`vs_current`) | `vs_current` or `sequential` |
| `comparison_dates` | `list[str] \| None` | no | Exact dates, newest = current. **Replaces** `reporting_period` when present. Capped at 3. |

### `NLResolveRequest`

| Field | Type | Required | Notes |
|---|---|---|---|
| `query` | `str` | yes | The user's question |
| `dimension` | `str \| None` | no | Which clarification is being answered: `"return"` or `"table"` |
| `clarification_answer` | `str \| None` | no | The chosen option's `id`, or the `"__skip__"` sentinel |
| `resolved_context` | `dict \| None` | no | Echoed back verbatim from the prior `needs_clarification` response — this is why no server-side session store is needed |

## Authentication

Every authenticated route takes `loginId` and `tenantId` as **query
parameters**. `tenantId` is sent in both modes (empty under 5.5), so the
frontend needs no per-host branching.

## Responses

### `GET /health`

```json
{"status":"ok","version":"6.0","profile":"iDEAL 6.0",
 "paths":{"detail":"tenant-scoped; not resolvable without a request (…)"}}
```

Under 5.5, `paths` is the full resolved path map from `profile.describe()`.
Under 6.0 it cannot be — paths depend on a tenant — so it carries that
explanation instead. That is expected output, not an error.

### `GET /app-config`

```json
{"version":"6.0","profile":"iDEAL 6.0","requires_tenant":true}
```

The frontend calls this at startup to choose its auth strategy, which is why
`VERSION` needs no frontend rebuild.

### `GET /auth/my-returns`

```json
{"login_id":"vaibhav@irisindia.net","tenant_id":"1001",
 "allowed_count":4,"allowed_forms":["2029","4070","4089","5001"]}
```

With `DV_AUTH_ENABLED=false` an unknown login returns `allowed_count: 0` rather
than 403.

### `GET /variance/find` — two shapes

**Exact match** — resolved, with the tables you can compute on:

```json
{"return_id":"4079",
 "return_name":"QCB_F013_Breakdown of exposures by geography",
 "report_freq":"D","tbl_path":"",
 "table_mapping_path":"D:\\Repo6\\1001\\DataBase\\4079\\TableMapping.xml",
 "tables":[{"table_name":"QCB_F013_BREAK_EXPOSURE","filter_col":"RDATE",
            "primary_column":"COUNTRY|RDATE","comp_filter_col_name":null,
            "PriID":"1|3","MandColumn":"","DesColName":"DESCRIPTION",
            "CodeColName":"CODE"}]}
```

Table entries carry both normalised keys (`table_name`, `filter_col`) and the
raw `TableMapping.xml` attributes (`TableName`, `PrimaryColumn`, `FilterColumn`).

**Fuzzy match** — candidates to disambiguate:

```json
{"candidates":[{"score":100,"return_id":"4079",
                "return_name":"QCB_F013_Breakdown of exposures by geography",
                "report_freq":"D","tbl_path":"","has_mapping":true}],
 "query":"F013"}
```

`404` when nothing matches; `422` when `return_name` is missing.

### `GET /variance/dates`

```json
{"dates":["05-AUG-2026","30-APR-2026","31-MAR-2022","31-MAR-2021","30-APR-2020"]}
```

Distinct reporting dates present in the table, newest first — the source for
the UI's date pickers.

### `POST /variance/compute`

Top level:

| Key | Type | Notes |
|---|---|---|
| `table_name` | `str` | The **physical** name, e.g. `IDEALCRILC.QCB_F013_BREAK_EXPOSURE_DP` |
| `reporting_date` | `str` | Echoed back |
| `comparison_periods` | `list[str]` | The dates actually compared against |
| `columns` | `list[str]` | Numeric value columns compared |
| `display_columns` | `list[str]` | Columns to show (differs when `selected_columns` was used) |
| `rows` | `list[dict]` | One per business row |
| `comparison_mode` | `str` | `vs_current` or `sequential` |
| `chain_dates` | `list[str]` | Populated in `sequential` mode |
| `missing_periods` | `list[str]` | Requested periods with no data |

Each row:

```json
{"identifier": "Australia",
 "display_label": "Australia",
 "current":  {"COUNTRY": "Australia", "SOVEREIGNS": 4172.0, "…": "…"},
 "previous": {"previous_1": {"SOVEREIGNS": {
     "value": 4172.0,
     "change":     {"value": "0.00",   "color": ""},
     "pct_change": {"value": "0.00%",  "color": ""},
     "variance_summary": {"text": "4,172.00  +0.00% (Prev: 4,172.00)",
                          "arrow": "", "color": ""}}}}}
```

`change`/`pct_change`/`variance_summary` are **pre-formatted for display** —
`color` is `"success"`, `"danger"` or `""`, and `arrow` is `▲`, `▼` or `""`.
The renderer does no arithmetic.

**No data is not an error.** When the table has no rows for that date the route
returns `200` with a bare error field:

```json
{"error":"No data found for IDEALCRILC.QCB_F013_BREAK_EXPOSURE_DP on 31-MAR-2025"}
```

so clients must check for `error` before reading `rows`.

### `POST /variance/nlresolve`

On success, the computed variance payload above, plus the resolution the NLP
layer settled on. When it cannot resolve confidently it returns a
**clarification** instead — see below.

### Clarification responses

```json
{"needs_clarification": true,
 "dimension": "return",
 "question": "Which return did you mean?",
 "options": [{"id": "4079", "label": "QCB_F013_Breakdown of exposures by geography"}],
 "skippable": true,
 "allow_other": true,
 "confidence": 0.0,
 "resolved_context": {"query": "…"}}
```

`dimension` is `"return"` or `"table"`. To answer, POST to the same route with
`dimension`, `clarification_answer` (an option `id`, or `"__skip__"` to make the
model pick), and `resolved_context` echoed back unchanged. `allow_other` appears
on the return dimension only. The table dimension carries a real `confidence`
and no `allow_other`.

### `POST /variance/nlquery`

```json
{"sql": "SELECT …", "columns": ["COUNTRY", "TOTAL"], "rows": [["Australia", 4172.0]]}
```

`rows` are positional arrays here, not objects. `422` when no valid SQL could be
generated (the detail carries the validation warnings); `404` when no accessible
table matches.

## Error shape

All errors are FastAPI's `{"detail": …}`. The mapping lives in
[backend/api/errors.py](../backend/api/errors.py) and is shared by every route:

| Exception | Status | `detail` |
|---|---|---|
| `FileNotFoundError` | 404 | `str(exc)` |
| `KeyError` | 404 | `str(exc)` — note this is **quoted**, e.g. `"\"Table 'X' not found…\""` |
| `HostProfileError` | 400 | `str(exc)` — operator-actionable (unknown tenant, unprovisioned tenant) |
| `RuntimeError` | 500 | `str(exc)` |
| anything else | 500 | `"Unexpected server error: <Type>: <exc>"` |

Request validation failures return FastAPI's standard `422` with a list of
per-field errors.

`HostProfileError` is 400 rather than 403 to keep it distinguishable from the
403s `require_login` raises for a *known but disallowed* caller.
