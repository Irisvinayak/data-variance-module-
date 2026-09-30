# Code Review Report — Data Variance Module

| | |
|---|---|
| **Scope** | Whole solution: `backend/` (FastAPI, data layer, auth, hosts, NLP/RAG pipeline), `frontend/` (React + Vite), config, tests, scripts |
| **Date** | 29-Sep-2026 |
| **Branch** | `6-host-profile-config` (working tree, uncommitted cleanup included) |
| **Method** | Manual line-by-line read-through against a standard FastAPI + RAG production checklist (the requested `fastapi_rag_code_review_guide.md` was not present in `docs/`), plus `ruff`, `vulture`, `pytest` and `npm run build`. Critical findings were re-verified by hand. |
| **Change policy** | Review only — **no code or logic was modified** for this report. |

**Checklist areas covered:** 1 Security & auth · 2 LLM / SQL safety · 3 RAG retrieval quality · 4 API design & validation · 5 Error handling · 6 Data layer · 7 Concurrency & caching · 8 Configuration · 9 Logging & observability · 10 Performance · 11 Testing & CI · 12 Frontend · 13 Code quality.

---

## 1. Executive summary

The codebase is **well-engineered in its internals and under-protected at its boundary**.

- **Strong:** a clean 5.5 / 6.0 host-profile abstraction, a thoughtful threadpool model, fail-closed caching of auth masters, access filtering applied *before* the LLM sees candidates, request correlation, and a structured AI audit trail. Comments explain *why* unusually well. Build and tests are green (106 tests pass, lint clean, frontend builds).
- **Weak:** the service **trusts identity from the query string** (no server-side token verification), the free-form **LLM-to-SQL route has a confirmed allow-list bypass**, diagnostics and error messages **disclose internals**, and there is **no rate limiting, response modelling, CI or dependency pinning**.

### Scorecard

| Area | Rating | Headline |
|---|---|---|
| Security & authentication | 🔴 Poor | Identity is unauthenticated; tenant/user enumeration; info disclosure |
| LLM / SQL safety | 🔴 Poor | `validate_sql` bypass via `TRIM(`/`EXTRACT(` stripping |
| RAG retrieval quality | 🟡 Fair | Sound hybrid design; thresholds uncalibrated, no index manifest |
| API design & validation | 🟡 Fair | No response models, weak input constraints, 200-with-error |
| Error handling | 🟡 Fair | Consistent mapping, but raw exception/ORA text returned to clients |
| Data layer | 🟡 Fair | Good cleanup discipline; silent row truncation, unbounded fallback connects |
| Concurrency & caching | 🟢 Good | Mostly locked, tenant-keyed; a few unlocked/unbounded caches |
| Configuration | 🟡 Fair | Clear version switch; `override=True`, unvalidated ints, insecure defaults |
| Logging & observability | 🟢 Good | Request ids + audit trail; PII in logs, no metrics |
| Testing & CI | 🔴 Poor | Security boundaries untested; no CI; no frontend tests |
| Frontend | 🟡 Fair | Safe rendering; token in URL, no security headers, god components |
| Code quality | 🟡 Fair | Readable, but several 300–450-line functions and 850+-line components |

### Findings by severity

| Critical | High | Medium | Low | Total |
|---|---|---|---|---|
| 2 | 19 | 36 | 24 | **81** |

### Top 5 actions (in order)

1. **Verify identity server-side** — validate the host JWT (signature, `exp`, `aud`) in `auth/deps.py` and derive `loginId`/`tenantId` from verified claims. Until then, bind the API to localhost / the reverse proxy only. *(SEC-01)*
2. **Close the `/variance/nlquery` SQL hole** — replace regex validation with an SQL parser (e.g. `sqlglot`), and run that route on a **read-only Oracle user granted only the permitted tables**. Or disable the route: the shipped UI does not call it. *(LLM-01)*
3. **Stop leaking internals** — generic client errors plus `X-Request-ID`, no tenant list in messages, lock down `/health`, `/variance/nlp-health`, `/auth/my-returns` and `/docs`. *(SEC-03…07)*
4. **Add guard rails** — rate limits, request-size and field-length limits, pydantic constraints, and an explicit "truncated" flag when the row cap is hit. *(API-*, DATA-01)*
5. **Build a safety net** — CI with pytest, ruff, pip-audit and bandit; pinned dependencies; tests for auth, routes and adversarial `validate_sql` cases. *(TEST-*)*

---

## 2. Strengths (keep these)

- **Host abstraction.** All 5.5 vs 6.0 differences live in `backend/hosts/`. Tenant is part of every cache key, and 6.0 tenants are validated against the registry *and* the provisioned folder.
- **Authorization before AI.** The retriever filters candidates to the caller's allowed returns before top-K and before any LLM call. On `/nlresolve` the LLM's answer is validated back against that shortlist, and every client-supplied return/table id is re-checked with `require_return_access`.
- **Client paths are never trusted.** The mapping file is located from the return's own `TblPath` (`service._trusted_tbl_path`), never from the `table_mapping_path` the browser echoes back.
- **Fail-closed auth caching.** An unreadable user/department master is not cached (`_MasterUnavailable`), and "user not found" (`None`) is kept distinct from "no grants" (`set()`).
- **Concurrency model.** Blocking routes are `def` (threadpool) with a documented reason; NLP imports are lazy, so a box without FAISS still serves the manual routes.
- **Intent-resolution resilience.** A circuit-breaker cooldown, a deterministic fallback, a single retry only on a bad answer, and tight timeouts and token limits.
- **Observability.** A ContextVar request id propagated into the threadpool and echoed as `X-Request-ID`; uvicorn logs adopted into the daily files; a JSONL audit record per LLM call.
- **Safe rendering in the UI.** No `dangerouslySetInnerHTML`/`innerHTML`, no open redirects; server strings are rendered as React text.
- **DB hygiene.** Cursor and connection are always released in nested `finally`; per-call timeout; a row cap; column identifiers validated and the return code quoted as a literal.

---

## 3. Detailed findings

Format: **ID · Severity · Location** — issue → impact → recommendation.
Locations are `file:line` where verified, otherwise `file` + function.

### 3.1 Security & authentication

**SEC-01 · 🔴 Critical · `backend/auth/deps.py:46-123` (`require_login`)**
The caller's identity is `?loginId=&tenantId=` taken on trust from the query string. No token or session is verified server-side; the module's own security note (lines 8-12) acknowledges this. On the frontend, the 6.0 JWT is decoded without verification and **never sent** to the backend (`frontend/src/api.js:36` sends no `Authorization` header).
→ Anyone who can reach the port can act as any user in any tenant by editing the URL.
→ Verify the JWT server-side (signature, `exp`, `aud`, issuer); take the login/tenant from its claims; send it as `Authorization: Bearer`. As an interim control, bind to `127.0.0.1` and allow only the IIS reverse proxy.

**SEC-02 · 🟠 High · `backend/api/auth.py` (`my_returns`, `/auth/my-returns`)**
Returns the allowed return ids of whoever the caller claims to be. Its docstring says "Remove or restrict to internal IPs in production".
→ Combined with SEC-01, this maps every user's entitlements.
→ Remove it, or restrict it to an admin role or an internal network.

**SEC-03 · 🟠 High · `backend/hosts/ideal_60.py:127-131` → `backend/auth/deps.py:107-112`**
The unknown-tenant error lists **every active tenant id** (`active: [...]`) and is returned verbatim as the 403 `detail`.
→ Tenant enumeration by any caller.
→ Return "Tenant not authorised"; keep the detail in the log.

**SEC-04 · 🟠 High · error leakage — `backend/main.py` (`unhandled_exception_handler`), `backend/api/errors.py`, `backend/data/db.py:171-187`, `backend/api/nlp.py` (`variance_nlquery`)**
Raw exception text, ORA error messages, schema-qualified table names, filesystem paths and (for `/nlquery`) generated SQL reach the client in `detail`.
→ Reveals the schema, the internal layout and the validation rules to an attacker.
→ Return a generic message plus the request id; log the specifics.

**SEC-05 · 🟠 High · `backend/api/meta.py` (`/variance/nlp-health`, `/health`)**
Unauthenticated. `nlp-health` exposes the index folder, model name and package errors, and `?check_model=true` lets anyone force-load the ~1.3 GB embedding model. Under 5.5, `/health` returns every repository path (`profile.describe`).
→ Information disclosure and a cheap way to create load.
→ Liveness returns only `{"status": "ok"}`; put detailed diagnostics behind auth or an IP allow-list.

**SEC-06 · 🟠 High · `backend/api/variance.py` (`variance_find`) → `backend/data/service.py` (`find_return_and_tables`)**
`/variance/find` does not filter by allowed returns (by design, per its docstring). It returns names, table lists and the server-side `table_mapping_path` for returns the user cannot open.
→ Metadata disclosure across entitlements.
→ Filter through `get_allowed_form_ids`; stop returning server paths.

**SEC-07 · 🟡 Medium · `backend/main.py:37-42`**
`/docs`, `/redoc` and `/openapi.json` are enabled and unauthenticated in every environment.
→ Disable them in production (`docs_url=None, redoc_url=None, openapi_url=None`).

**SEC-08 · 🟡 Medium · `backend/auth/deps.py:60-88` — the `DV_AUTH_ENABLED=false` bypass**
The kill switch disables all authorisation with only a one-time WARNING. Because `load_dotenv(override=True)` (see CFG-01), a stale developer `.env` can silently override a production environment variable.
→ Honour it only when an explicit `ENV=dev` is set and the server is bound to localhost; otherwise refuse to start.

**SEC-09 · 🟡 Medium · path traversal — `backend/data/service.py` (`_load_table_mapping`), `backend/hosts/base.py:117-128`**
The client-supplied `return_id` is joined into filesystem paths, the directory is listed, and any `*mapping*.xml` found is parsed. With auth on, the exact-id access check blocks this; with auth off under 5.5, `..\..\X` walks outside the repository.
→ Validate `return_id` against `^\d+$` or the returns master, and assert the normalised path stays under the base directory.

**SEC-10 · 🟡 Medium · SQL identifiers — `backend/data/service.py` (`_distinct_dates_desc`, diagnostics), `backend/data/calculate_variance.py` (`build_query`), `backend/nlp/date_resolver.py`**
`table_name`, `filter_col`, `return_code_col`, `freq_col` and `DP_TABLE_SCHEMA` are f-string interpolated. Today their provenance is the trusted repository XML, and dates are `strptime`-parsed, so this is not exploitable now. But nothing enforces it, and no bind variables are used anywhere.
→ Apply `is_safe_identifier` to every identifier; use bind variables for dates and literals.

**SEC-11 · 🟡 Medium · `backend/nlp/index_store.py:35`, `backend/nlp/lexical_search.py:63`**
`pickle.load` on index artifacts dropped in by an external tool.
→ Arbitrary code execution if `artifacts/nlp-index/` is writable by anyone else.
→ Lock down directory permissions and verify hashes via a manifest; prefer JSON/npz.

**SEC-12 · 🟢 Low · `backend/data/xml_loader.py`**
`xml.etree` on repository XML. Modern expat limits entity expansion and the input is server-side, so the risk is low. `defusedxml` is a cheap hardening.

**SEC-13 · 🟢 Low · `backend/main.py:44-54`**
CORS allows all methods and headers; the API uses only GET and POST. Narrow them.

**SEC-14 · 🟢 Low · `backend/main.py:74`**
The inbound `X-Request-ID` is only length-capped. Restrict it to `[A-Za-z0-9-]` to prevent log forging.

### 3.2 LLM & SQL safety (`/variance/nlquery`, `/variance/nlresolve`)

**LLM-01 · 🔴 Critical · `backend/nlp/sql_generator.py:718-719` (`validate_sql`)** — *verified by reading the code*
Before the table allow-list check, the validator deletes `extract\s*\([^)]*\)` and `trim\s*\([^)]*\)`. `[^)]*` consumes everything up to the first `)`, including a nested sub-query. For example, in `SELECT TRIM((SELECT username FROM all_users WHERE rownum=1)) x, code FROM allowed_t`, the inner `FROM all_users` is removed before `_FROM_JOIN_RE` runs. The column check (lines 738-746) is built from the same stripped text, so it passes too.
→ Any table or dictionary view the Oracle account can read is retrievable through `/variance/nlquery`, if a prompt-injected question gets the model to emit this shape (see LLM-02). *(The payload has not been executed against Oracle.)*
→ ① Validate with a real SQL parser (e.g. `sqlglot`, Oracle dialect) and walk every table reference in the AST. ② Run this route on a **read-only DB user granted only the allowed tables** — that is the only boundary that cannot be bypassed. ③ If the route is not needed (the shipped UI never calls it), disable it.

**LLM-02 · 🟠 High · prompt injection — `backend/nlp/intent_resolver.py:123,177`, `backend/nlp/sql_generator.py` (prompt builder)**
The raw user question is interpolated into prompts (`Question: "{query}"`) without delimiting or escaping; the resolved-time-context lines also echo user text.
→ A user can steer the model, for example to produce the LLM-01 payload.
→ Put the question in a clearly delimited data block marked as data, and strip or escape quotes and newlines. Do not rely on the prompt for safety: validate the output structurally (LLM-01).

**LLM-03 · 🟠 High · `backend/nlp/nlp_config.py:217-218`**
The default `DV_NLP_OLLAMA_URL` is a **public IP over plain HTTP** with no authentication (`http://3.109.51.228/OllamaProxy/...`).
→ The user's questions, schema text and row labels travel in cleartext; the model's reply, which becomes SQL, can be altered in transit.
→ No default (fail closed if unset); HTTPS plus a token; keep it on a private network.

**LLM-04 · 🟠 High · `backend/nlp/sql_generator.py:556-596`**
The SQL-generation path has no circuit breaker (unlike `intent_resolver`, lines 46-190). During an Ollama outage every `/nlquery` blocks a worker for the full timeout, plus a retry.
→ Exhausts the threadpool and starves the manual routes.
→ Reuse the cooldown breaker; cap concurrent LLM calls with a semaphore.

**LLM-05 · 🟠 High · `backend/nlp/sql_generator.py:591`, `backend/nlp/intent_resolver.py:229`**
`response.json()` is called outside the handled error path.
→ An HTML error page from the proxy produces a `JSONDecodeError` and a 500, with no fallback and no cooldown.
→ Catch `ValueError` and route it to the existing failure/fallback handling.

**LLM-06 · 🟠 High · `backend/api/nlp.py` (`variance_nlquery`)**
`/nlquery` skips the confidence gates used by `/nlresolve`, returns the raw Oracle error and the generated SQL to the client, and authorises per table only (no row-level scoping, if any is required — to be confirmed).
→ Apply the same confidence gate, return a generic error, and confirm whether row-level scoping is needed.

**LLM-07 · 🟡 Medium · `backend/nlp/sql_generator.py:738-760`**
False rejections: functions not in the allow-list (`ROUND`, `CAST`, `DECODE`, `ABS`) are flagged as "hallucinated columns", and quote-stripping rejects labels containing `--`, `@` or `;`.
→ Legitimate questions fail. A parser-based validator (LLM-01) fixes both directions.

**LLM-08 · 🟡 Medium · `backend/nlp/nlp_config.py:282,286`**
`num_predict: 128` for the SQL models is tight for multi-column or self-join SQL, and the retry re-sends the full prompt.
→ Truncated SQL, then a validation failure.
→ Raise the limit and send a compact retry prompt.

**LLM-09 · 🟡 Medium · `backend/nlp/intent_resolver.py` (fallback path), `backend/api/nlp.py` (response)**
When the deterministic fallback replaces the LLM, the response does not say so and the reported confidence is unchanged.
→ Add a `resolution_mode: "llm" | "fallback"` field so users and operators can tell.

**LLM-10 · 🟢 Low · `backend/nlp/intent_resolver.py:241`**
The greedy `\{.*\}` JSON extraction breaks when the reply contains two objects, and `selected_columns` is not type-checked.
→ Use `json.JSONDecoder().raw_decode` from the first `{`; validate types.

### 3.3 RAG retrieval quality

**RAG-01 · 🟡 Medium · `backend/nlp/index_store.py` (loaders)**
`.faiss` and `.pkl` are loaded independently. Nothing checks `index.ntotal == len(meta)`, that the index dimension matches the embedder, or the metric type.
→ A partially copied index rebuild produces an `IndexError` or silently wrong records.
→ Ship a manifest (model, dim, counts, hashes); validate on load; swap directories atomically.

**RAG-02 · 🟡 Medium · `backend/nlp/nlp_config.py:22`**
Nothing ties `DV_NLP_EMBED_MODEL` to the model that built the index.
→ Changing the model silently makes the similarity scores meaningless.
→ Record the model in the manifest and assert it at startup.

**RAG-03 · 🟡 Medium · `backend/nlp/confidence.py`**
Tie detection uses min-max normalised scores, so it depends on the score spread: with two candidates it fires only on exact equality. A metadata penalty can push the top score to ≤ 0, after which the margin is treated as full separation.
→ Real near-ties auto-proceed and confidence is inflated.
→ Use a relative gap `(top1 − top2) / top1` on raw scores; clip before computing the margin.

**RAG-04 · 🟡 Medium · `backend/nlp/nlp_config.py:156-157`, `backend/nlp/retriever.py`**
BGE cosine scores cluster high, so `MIN_TABLE_SCORE=0.25` and `MIN_COLUMN_SCORE=0.20` effectively filter nothing; row-label hits have no floor.
→ Calibrate the thresholds on the benchmark (`scripts/eval_retrieval.py`) and record the calibration.

**RAG-05 · 🟡 Medium · `backend/nlp/embedder.py:23-60`**
The ~1.3 GB model loads lazily on the first NL request (or the first `nlp-health?check_model=true` call, `api/meta.py`), under a lock; nothing warms it at startup.
→ The first requests stall for tens of seconds.
→ Warm it up in a FastAPI `lifespan` hook when NLP is enabled.

**RAG-06 · 🟢 Low · `backend/nlp/scoped_retriever.py`**
BM25 takes the global top-15 and then filters to the scope, so scoped tables can end up with no lexical signal. Score within the subset instead.

**RAG-07 · 🟢 Low · `backend/nlp/query_normalizer.py`**
Short-token rewrites ("os", "rest", "dom", "acc") can change a query's meaning. Require context or remove these entries.

### 3.4 API design & validation

**API-01 · 🟠 High · every route; `backend/data/models.py`**
No rate limiting. `NLResolveRequest.query` has no length limit, and `resolved_context` accepts an arbitrary dict. Each NL call costs an embedding, an LLM call and several Oracle queries.
→ A small flood exhausts the threadpool, the DB pool and the LLM proxy.
→ Rate-limit per user and per IP (at IIS or with `slowapi`), use `Field(max_length=…)`, and cap the body size.

**API-02 · 🟡 Medium · `backend/data/models.py:12-40`**
Missing constraints: `reporting_period` may be 0 or negative (only `> 12` is checked in `api/variance.py`); `comparison_mode` is a free string, and unknown values silently behave as `vs_current`; `reporting_date` has no pattern; `table_mapping_path` is required but ignored.
→ Use `conint(ge=1, le=12)`, `Literal["vs_current", "sequential"]` and `Field(pattern=r"^\d{2}-[A-Z]{3}-\d{4}$")`; make `table_mapping_path` optional and deprecated.

**API-03 · 🟡 Medium · `backend/api/variance.py` (`variance_compute`), `backend/data/calculate_variance.py`**
`calculate_variance` returns `{"error": ...}`. `/compute` passes it through with **HTTP 200**, while `/nlresolve` turns the same dict into a 500. "No data found" and "invalid date" are client errors (404/422), not 200 or 500.
→ Raise typed domain exceptions and map them in `api/errors.py`. *(This changes the contract, so coordinate with the frontend.)*

**API-04 · 🟡 Medium · every route**
No `response_model`; every handler returns a bare `dict`. The OpenAPI response schemas are empty and nothing guards the output shape.
→ Add pydantic response models (the shapes are already documented in `docs/models.md`).

**API-05 · 🟢 Low**
No API versioning (`/v1`); `connection_string` is always `None` and is dead. Keep them in mind for the next contract revision.

### 3.5 Error handling

**ERR-01 · 🟡 Medium**
Two error styles coexist: exceptions (service layer) and returned `{"error": ...}` dicts (variance engine). Callers must remember to check both; `/nlresolve` checks `.startswith("No data found")` on a message string. → Unify on exceptions (see API-03).

**ERR-02 · 🟢 Low · `backend/nlp/*`**
Many broad `except Exception` blocks. They all log and degrade on purpose, which is appropriate for optional signals, but a few (see LLM-05) sit outside the guarded region. Add a unit test per fallback path.

### 3.6 Data layer (Oracle)

**DATA-01 · 🟠 High · `backend/data/db.py:158-168`**
`fetchmany(DB_MAX_ROWS)` truncates silently; only a log warning is written.
→ `/compute` works out variances from a partial row set and returns **wrong numbers with a 200**; `/nlquery` returns truncated data unflagged.
→ Fetch `cap + 1` rows; when truncated, return an error or a `truncated: true` flag.

**DATA-02 · 🟠 High · `backend/data/db.py:84-118`**
The pool uses the default WAIT mode with no `wait_timeout`, and any acquire error falls back to an **unpooled** `oracledb.connect` (line 111). That path has no limit, and it bypasses the NLS session callback, so the date format differs on it. A failing `create_pool` is retried on every request.
→ Under load or a pool fault, the DB is flooded with direct sessions that use different NLS settings.
→ Use `getmode=POOL_GETMODE_TIMEDWAIT` with a `wait_timeout`; remove or cap the fallback and apply the NLS settings there too.

**DATA-03 · 🟡 Medium · `backend/data/service.py` (`_run_table_diagnostics`)**
Every zero-row result runs `COUNT(*)` and a DISTINCT over the whole table inside the request.
→ Doubles or triples latency on large tables, and a caller can trigger it at will.
→ Gate it behind DEBUG or move it off the request path.

**DATA-04 · 🟢 Low · `backend/data/db.py:52-74`**
The NLS session settings were silently broken until commit `ff0ba97`. Add a test that asserts they are applied.

### 3.7 Concurrency & caching

**CONC-01 · 🟡 Medium · `backend/auth/service.py` (`_forms_cache`), `backend/data/service.py` (`_mapping_cache`)**
Unbounded caches; negative results (unknown login → `None`) are cached for `AUTH_TTL_SEC`.
→ Random `loginId` values grow memory without bound.
→ Use a bounded TTL/LRU cache (`cachetools.TTLCache`); cache negatives briefly or not at all.

**CONC-02 · 🟡 Medium · `backend/data/report_lookup.py` (`_TTLCache`)**
No lock; three dicts are updated one after another, so a reader can see new data with an old timestamp. Concurrent misses all rebuild at once (thundering herd). The same get-then-build pattern appears in `auth/service.py` and `hosts/ideal_60.py`.
→ Store one tuple per key under a lock; single-flight rebuilds.

**CONC-03 · 🟡 Medium · `backend/nlp/return_lookup.py`**
The first build holds the global lock while walking every XML file, blocking lookups for all tenants.
→ Use a per-key lock or future.

**CONC-04 · 🟢 Low · `backend/nlp/sql_generator.py`, `backend/nlp/intent_resolver.py`**
A shared `requests.Session` uses the default pool of 10 connections; size it to the threadpool.

### 3.8 Configuration

**CFG-01 · 🟡 Medium · `backend/config/settings.py:22-25`**
`load_dotenv(override=True)`: the `.env` file beats real OS or orchestrator variables, including secrets and `DV_AUTH_ENABLED`.
→ Use `override=False` so the deployment environment wins.

**CFG-02 · 🟡 Medium · `backend/config/settings.py`, `backend/data/db.py:36-43`, `backend/logging_config.py:41`, `backend/nlp/nlp_config.py`**
Bare `int()`/`float()` on environment values at import time. A typo crashes startup with a `ValueError` that doesn't name the variable.
→ Move to `pydantic-settings` for typed, validated, self-describing configuration.

**CFG-03 · 🟡 Medium · `backend/config/settings.py` (`SERVER_HOST`)**
Defaults to `0.0.0.0`, which is exposed on every interface; with SEC-01 that is significant.
→ Default to `127.0.0.1` behind IIS.

**CFG-04 · 🟢 Low · `.env` / `.env.example`**
`DOTNET_API_URL` exists in `.env` but is read by no code. `.env.example` carries the public Ollama IP (LLM-03); use a placeholder.

### 3.9 Logging & observability

**LOG-01 · 🟡 Medium · PII — uvicorn access log (via `logging_config._adopt_uvicorn_loggers`), `backend/api/nlp.py` INFO lines, `backend/ai_audit.py`**
Identity travels in the URL, so every access-log line records the `loginId` (an email under 6.0) and the `tenantId`. Full natural-language questions and generated SQL, with inlined values, are logged at INFO and audited, with 30-day retention and no redaction.
→ Move identity to a header or token, redact query strings in access logs, restrict access to the log folder, and document retention.

**LOG-02 · 🟡 Medium · `backend/logging_config.py`**
Side-by-side 5.5 and 6.0 processes write to the same `logs/<date>.log` by default, and both prune it; lines interleave with no instance marker.
→ Default to `logs/<version>/`, or add the version to the format.

**LOG-03 · 🟢 Low**
No metrics (latency, LLM, DB timings) and no readiness probe that checks the DB and the LLM. Add Prometheus metrics and a `/ready` endpoint.

### 3.10 Performance

**PERF-01 · 🟢 Low · `backend/nlp/schema_info.py`, `backend/nlp/ranking.py`, `backend/nlp/sql_generator.py`**
Per-request linear scans over all columns and returns (`columns_for_table`, repeated `parse_returns` iteration).
→ Precompute table→columns and id→return maps when the cache loads.

**PERF-02 · 🟢 Low · frontend bundle**
One 600 kB chunk (recharts loaded eagerly).
→ Lazy-load `VisualizationPanel` / `manualChunks`.

### 3.11 Testing & CI

**TEST-01 · 🟠 High · `tests/`**
106 tests, mostly NLP date parsing and cache hygiene, plus 7 data-layer regressions. **None** cover the security boundaries: `require_login`/`require_return_access`, 6.0 tenant validation, routes via `TestClient`, `validate_sql` (adversarial cases), the retriever's access filter, intent parsing and fallback, or `compute_variance` end-to-end with a stubbed DB.
→ Start with adversarial `validate_sql` tests and auth-dependency tests.

**TEST-02 · 🟡 Medium · repository root**
No CI pipeline (no `.github/`, no Azure DevOps YAML), no type checking, no `pip-audit`/`bandit`.
→ Add a pipeline: `pytest`, `ruff`, `mypy` (gradually), `pip-audit`, `bandit`, `npm ci && npm run build`.

**TEST-03 · 🟡 Medium · `requirements.txt`, `frontend/package.json`**
Python dependencies use `>=` with no lock file; npm uses caret ranges.
→ Builds are not reproducible, and a major release of fastapi, pydantic, sentence-transformers or recharts can break production.
→ Pin with `pip-tools`/`uv` lock files; use `npm ci` in deploys.

**TEST-04 · 🟡 Medium · `frontend/`**
No ESLint (so `react-hooks/exhaustive-deps` is not enforced), no Vitest/RTL tests, no TypeScript or PropTypes.

### 3.12 Frontend

**FE-01 · 🟠 High · `frontend/src/auth/strategy60.js` (`TOKEN_KEY = '_at'`, line 40)**
The JWT arrives in the query string. It is removed with `replaceState` only after the bundle loads, so the initial `index.html` and asset requests carry it into IIS and proxy logs and browser history. It is then kept in `sessionStorage` with no `exp` check.
→ Have the host pass it in a URL fragment or POST, or exchange a one-time code for an HttpOnly cookie; check `exp` and clear the token when it expires.

**FE-02 · 🟠 High · `frontend/public/web.config`, `web.60.config`**
No security headers (checked: no `customHeaders` element): no CSP, `frame-ancestors`, HSTS, `X-Content-Type-Options` or `Referrer-Policy`.
→ Add them. The app runs in an iFrame, so use `frame-ancestors <host-origin>` rather than DENY; add `Referrer-Policy: no-referrer`.

**FE-03 · 🟠 High · `frontend/public/` + `package.json` build scripts**
Both `web.config` and `web.60.config` ship in every build; a 6.0 deploy needs a manual rename. If that step is forgotten, IIS proxies the 6.0 UI to the 5.5 backend on port 8002.
→ Emit the correct `web.config` per build mode (a small Vite plugin or post-build copy).

**FE-04 · 🟠 High · `frontend/src/api.js:36`**
No timeout or AbortController on any request. `/nlresolve` (LLM) and `/compute` can hang indefinitely, leaving the UI stuck in loading.
→ Use `AbortSignal.timeout(n)` combined with a caller-supplied signal; abort on reset or unmount.

**FE-05 · 🟠 High · `frontend/src/components/LayoutContainer.jsx` (`handleFindReturn`, `handleSelectCandidate`)**
`/compute` and `/nlresolve` guard against stale responses with `requestSeqRef` (lines 419-610), but the find and select-candidate handlers do not.
→ A slow `/find` can overwrite the state after the user has reset or started another action.
→ Apply the same sequence check there.

**FE-06 · 🟡 Medium · `frontend/src/components/LayoutContainer.jsx:288`**
`isAllowed()` returns `true` when `allowedFormIds.size === 0`, and a failed `/auth/my-returns` sets an empty set, so the dropdown **fails open**. The server still enforces access on `/dates`, `/compute` and `/nlresolve`, so this exposes listings only.
→ Fail closed and show "permissions unavailable" on error.

**FE-07 · 🟡 Medium · `frontend/src/App.jsx`**
If `bootstrapAuth` rejects, or `/app-config` hangs, the app stays on "Starting…" forever; an empty login renders the app with no "not signed in" state.
→ Add error and timeout states with a retry.

**FE-08 · 🟡 Medium · `frontend/src/auth/index.js`, `strategy60.js`**
The production bundle logs identity values and configuration hints (including how to set `tenantId`) to the console, and the strategy can fall back to guessing the version from the URL shape.
→ Gate the logs on `import.meta.env.DEV`; fail hard in production when `/app-config` is unavailable.

**FE-09 · 🟡 Medium · `LayoutContainer.jsx` (873 lines, ~25 `useState`), `ControlBar.jsx` (827 lines, ~30 props)**
God components with heavy prop drilling; every keystroke in the NL box re-renders the whole toolbar.
→ Extract hooks (`useVarianceWizard`, `useNlpResolve`, `usePermissions`) or a `useReducer` state machine; split ControlBar's sub-components into their own files; memoise the heavy result panels.

**FE-10 · 🟡 Medium · `LayoutContainer.jsx` (dates effect)**
The effect depends on the `returnInfo` object's identity and clears the selected dates. Re-setting an equal object refetches and wipes the user's picks, and a failed `/dates` call shows "No data found" instead of an error.
→ Key the effect on `return_id` and `table_name`; add an error state.

**FE-11 · 🟡 Medium · accessibility — `ControlBar.jsx` dropdowns and icon buttons**
No `aria-expanded`/`aria-haspopup`/listbox roles, no Escape or arrow-key handling, no focus return; icon-only buttons (🎤 🔍 ✕ →) have no `aria-label`; the NL input has no label.
→ Follow the WAI-ARIA combobox pattern.

**FE-12 · 🟢 Low · `LayoutContainer.jsx:637`**
`handleVoiceInput = () => {}`: the 🎤 button does nothing. Hide it until the feature exists.

**FE-13 · 🟢 Low**
The search button stays enabled while a request is in flight, so repeated submits pile up on the backend.

**FE-14 · 🟢 Low**
Colours are hard-coded (JS and about 140 hex literals in CSS), and one chart tick is `#000000` while the others are `#8b949e`; there are magic numbers (score-badge thresholds, 8000 ms toast); two global stylesheets use ad-hoc prefixes.
→ Use CSS custom properties and a constants module.

**FE-15 · 🟢 Low**
Duplicate structures: `DisambigDropdown` vs `NlpReturnPicker`, and two near-identical table renderers in `DataVarianceTable.jsx`. Extract a generic searchable dropdown and a single table renderer.

**FE-16 · 🟢 Low · `.env.production*`**
`VITE_PROXY_TARGET` has no effect in production builds and misleads operators.

### 3.13 Code quality & maintainability

**CQ-01 · 🟡 Medium · `backend/api/nlp.py` (`variance_nlresolve`, ~450 lines)**
Mixes the clarification state machine, authorisation, retrieval, date resolution, a retry and response shaping; authorisation is called from several branches, so a new branch can easily miss it.
→ Move it into a service-layer pipeline with **one** authorisation gate at the point the return is finally chosen.

**CQ-02 · 🟢 Low · large functions**
`service._load_table_mapping` (~170 lines, 15+ candidate paths), `calculate_variance` (~340 lines), `retriever.get_relevant_schema` (~390 lines), `sql_generator.py` (762 lines, with an Ollama client duplicated from `intent_resolver`).
→ Extract steps and share one LLM client module.

**CQ-03 · 🟢 Low · magic numbers in ranking and confidence**
Weights such as 2.0/1.0/0.03, 0.45/0.40/0.15 and a 0.25 penalty are inline. Move them to `nlp_config.py` next to the other tunables.

**CQ-04 · 🟢 Low · comments**
Very dense and often narrate history (branch names, measured counts, "used to…"). Valuable today, but it will drift. Move the history into `docs/` and commit messages; keep only the *why* in code.

**CQ-05 · 🟢 Low · log prefixes**
Routes moved to `backend/api/` still log as `[main]`. Use per-module prefixes, or rely on `%(name)s`, which is already in the format.

**CQ-06 · 🟢 Low · dead or unused code**
`auth/service.py` role-access functions (`get_user_role_id`, `validate_create_instance_access`, `can_generate_instance`, `invalidate_role_cache`), `Ideal60Profile.invalidate_tenant_registry`, `QueryAnalysis.has_date_intent`, and the legacy `_rrf`/`_tokens` aliases in the retriever. The role-access code is documented as planned 6.0 work; decide whether to wire it up or remove it.

---

## 4. Remediation roadmap

| Phase | Items | Effort |
|---|---|---|
| **Now (before any wider exposure)** | SEC-01 interim (bind to localhost / proxy only) · LLM-01 (read-only DB user and/or disable `/nlquery`) · SEC-02 · SEC-03 · SEC-05 · SEC-07 · LLM-03 | ~1–2 days |
| **Next sprint** | SEC-01 full (server-side JWT) with FE-01 · SEC-04 · API-01 · DATA-01 · DATA-02 · LLM-04/05 · FE-02/03/04 · TEST-01 (auth + `validate_sql`) · TEST-02/03 | ~1–2 weeks |
| **Following** | API-02/03/04 · CFG-01/02 · CONC-01/02 · RAG-01/02/03/04 · LOG-01 · FE-05…11 · CQ-01 | ~2–4 weeks |
| **Backlog** | All remaining Low items | as capacity allows |

---

## 5. Verification notes

- `python -m pytest -q`: **106 passed**. `ruff` (F, E9, B): clean. `npm run build`: succeeds (warning: 600 kB chunk).
- LLM-01 was confirmed by reading `validate_sql` (lines 718-746); the payload has not been executed against Oracle.
- Findings marked "to be confirmed" depend on business rules (e.g. row-level scoping) or runtime environment details not visible in the code.
- This report made **no changes to code or logic**.
