# iDEAL Data Variance Module

**End-to-end technical architecture, data preparation, AI models, functionalities, and production documentation**

Production Handover · Traced from Source

This document is organized around real data flows through `data-variance-module-` (FastAPI backend + React SPA). It is the companion to the iDEAL Chatbot handover and uses the same structure. Claims carry a `file:line` or file citation back to source. Anything that could not be confirmed from code is marked **needs verification**.

Deeper references that already exist in the repo, and which this document summarizes rather than replaces:
[architecture.md](architecture.md) · [functionality.md](functionality.md) · [models.md](models.md) · [logging.md](logging.md) · [integration-plan.md](integration-plan.md)

---

## Contents

1. [Complete System Flow](#1--complete-system-flow)
2. [Source Data → Prepared Data](#2--source-data--prepared-data)
3. [Common Runtime Request Flow](#3--common-runtime-request-flow)
4. [Functional Flows](#4--functional-flows)
5. [Complete AI Model Inventory](#5--complete-ai-model-inventory)
6. [NLP Layer — Failure Handling Reference](#6--nlp-layer--failure-handling-reference)
7. [Frontend & API Flow](#7--frontend--api-flow)
8. [Deployment & Configuration Flow](#8--deployment--configuration-flow)
9. [Logging, Debugging & Testing](#9--logging-debugging--testing)
10. [Security Flow](#10--security-flow)
11. [Reference Matrices](#11--reference-matrices)
12. [Worked End-to-End Examples](#12--worked-end-to-end-examples)
13. [Production Readiness](#13--production-readiness)
14. [Known Limitations & Troubleshooting](#14--known-limitations--troubleshooting)

---

## 1 · Complete System Flow

The module computes **period-over-period variance** for iDEAL regulatory returns, and answers natural-language questions about the same data. It is not a standalone product. It is embedded in an iDEAL host application (5.5 or 6.0). The host supplies the caller's identity and owns the XML metadata repository and the Oracle database that this module reads. **The module never writes** to either.

One codebase serves two hosts. `VERSION` in the root `.env` is the single switch. Each version runs as its **own OS process**.

| | iDEAL 5.5 | iDEAL 6.0 |
|---|---|---|
| Host app | ASP.NET MVC | React + .NET Web API |
| Tenancy | none — one repository | multi-tenant |
| Repository root | `DV_BASE_PATH_55` (default `D:\Repo5.5`) | `DV_BASE_PATH_60\<TenantId>` (default `D:\Repo6`) |
| Identity arrives as | `?loginId=` (a username) | JWT in `?_at=` (an email) |
| Returns master | `Database\Returns.xml` (`<Return>`) | `<tenant>\DataBase\Return.xml` (`<Row>`) |
| Allowed-returns delimiter | `\|` (`Forms="a\|b"`) | `,` (`ReturnId="a,b"`) |
| Oracle schema | `CRILC` (CIMS returns) | `IDEALCRILC` (QCB returns) |
| Backend port | **8004** | **8003** |
| IIS virtual directory | `/DataVar/` | `/DataVar6.0/` |
| Embedding index | `artifacts/nlp-index/ideal-55/` | `artifacts/nlp-index/ideal-60/` |
| Fiscal year start | April | January |

```
SOURCE DATA                              PREPARATION                        RUNTIME (this repo)
───────────                              ───────────                        ───────────────────
iDEAL XML repository                     none — read live per request  ──▶  backend/hosts/ (path + shape per version)
 (Returns.xml / Return.xml,                                                  backend/data/report_lookup.py
  <return>\TableMapping.xml,                                                 backend/auth/service.py
  XML_Query.xml, user/dept masters,
  period master, XML_Tenant.xml)

Oracle regulatory tables  ──────────────  none — SELECT at request time ──▶  backend/data/db.py → calculate_variance.py

Oracle schema + row samples  ──▶  EXTERNAL "Embedding maker" tool       ──▶  artifacts/nlp-index/ideal-55|ideal-60
                                  (D:\Tools\Embeding maker\...)              read-only: backend/nlp/index_store.py
                                  FAISS + BM25 + schema.json + QA pairs

Self-hosted Ollama proxy  ─────────────────────────────────────────────────▶  intent_resolver.py (qwen2.5:7b)
                                                                              sql_generator.py   (sqlcoder-7b-2)
```

### Master data-lineage diagram

```
 USER (browser, launched from the iDEAL host page)
   │  5.5: /DataVar/?loginId=iris810         6.0: /DataVar6.0/?_at=<JWT>&_lid=<login>
   ▼
 React SPA (frontend/src)
   │  bootstrapAuth(): GET /app-config → picks strategy55 / strategy60      [src/auth/index.js]
   │  every call carries ?loginId=…&tenantId=…                              [src/auth/index.js withAuth()]
   ▼
 IIS  web.config rewrite  ^api/(.*)  or  ^DataVar[6.0]/api/(.*)  → 127.0.0.1:<port>
   ▼
 FastAPI backend/main.py  (request-id middleware, CORS, exception handlers)
   ├─ Depends(require_login)  → HostProfile.validate_context → allowed return ids   [auth/deps.py:46]
   ├─ manual path:  /variance/find → /variance/dates → /variance/compute
   │     report_lookup (XML) → TableMapping.xml → db.execute_query (Oracle) → calculate_variance
   ├─ NL path:      /variance/nlresolve
   │     query_analyzer → retriever (FAISS+BM25, filtered to allowed returns)
   │     → confidence gate (ask vs proceed) → intent_resolver (LLM picks from shortlist)
   │     → date_resolver → the SAME compute_variance as the manual path
   ▼
 JSON (untyped dict — no response_model) → LayoutContainer → TablePanel / VisualizationPanel
```

---

## 2 · Source Data → Prepared Data

### 2.1 iDEAL XML repository — read live, never prepared

There is **no offline preparation step** for the XML metadata. Every file is read at request time through a `HostProfile` ([backend/hosts/base.py](../backend/hosts/base.py)), with TTL/mtime caches in front of it.

| File | 5.5 location | 6.0 location | Used for |
|---|---|---|---|
| Returns master | `Database\Returns.xml` | `<tenant>\DataBase\Return.xml` | Return name → id, `RepFreq`, `TblPath` |
| Non-XBRL returns master | `NonXBRLReturns.xml` | per profile | Second return list, with its own allowed-returns attribute |
| Table mapping | `<returnId>\TableMapping.xml` (located via `TblPath`) | same | Which Oracle tables a return has; `PrimaryColumn`, `FilterColumn`, `DesColName`, `CodeColName` |
| `XML_Query.xml` | per return | per return | Fallback table metadata when `TableMapping.xml` is missing or has empty `TableName` (CIMS_ALE family) — [backend/data/query_xml_lookup.py](../backend/data/query_xml_lookup.py) |
| User master | `XML_User` | per tenant | login → `DepartmentId` |
| Department master | `XML_Dept` | per tenant | `DepartmentId` → allowed return ids |
| Period master | — | per tenant | Fills an unusable `RepFreq` (blank or `x`) from `PeriodId` |
| Tenant registry | — | `XML_Tenant.xml` | Validates `tenantId` |

Every path can be pinned individually with a `DV_*_PATH` override ([backend/config/settings.py:167-177](../backend/config/settings.py#L167-L177)) without writing a new profile.

### 2.2 Oracle data — queried at request time

`compute_variance` resolves the **physical table name** first: it adds the `DP_TABLE_SCHEMA` prefix and, when `IS_SP_TABLE_DATA_ENABLED` is on and the return is not an Excel return, the `_DP` suffix. This mirrors the .NET host's own rule. It then runs a SELECT built by `calculate_variance.build_query()` ([backend/data/calculate_variance.py:391](../backend/data/calculate_variance.py#L391)).

- Pool: `oracledb` connection pool, `DV_DB_POOL_MIN=1`, `DV_DB_POOL_MAX=20`, sized against FastAPI's threadpool ([backend/data/db.py:36-43](../backend/data/db.py#L36-L43)).
- Per-call timeout `DV_DB_CALL_TIMEOUT_MS=60000`. Row cap `DV_DB_MAX_ROWS=5000`.
- No host or credential defaults. A missing value fails loudly at the first query rather than silently connecting somewhere else.

### 2.3 NLP embedding index — built externally, read-only here

Built by a **separate "Embedding maker" tool** that is not in this repository. The `ideal-60/build_stamp.json` records its source as `D:\Tools\Embeding maker\output\vectorDb6.0\index\...`. This project only reads the output. Index records carry only `{text, table, column}` and **no `return_id`**. The table → return mapping is resolved live against this app's own XML ([backend/nlp/return_lookup.py](../backend/nlp/return_lookup.py)), so the index can be rebuilt without touching authorization.

```
Build time (EXTERNAL tool)
  Oracle schema + sampled row-label values
    → schema.json, description_samples.json
    → FAISS: table_index / column_index / row_label_index / qa_index
    → bm25_table_index.pkl, qa_pairs.json
    → dropped wholesale into artifacts/nlp-index/ideal-55 | ideal-60

Runtime (this repo)
  nlp_config.index_dir_for(VERSION)   → folder chosen once at import       [nlp_config.py:81-96]
  embedder.embed_query()              → BAAI/bge-large-en, local, BGE query prefix
  index_store.search()                → FAISS search, cached per process
  lexical_search.search_bm25()        → BM25; silent no-op if file missing
```

| File | Role | ideal-55 | ideal-60 |
|---|---|---|---|
| `table_index.faiss` + `table_meta.pkl` | Table-description similarity (supporting signal) | ✅ | ✅ |
| `column_index.faiss` + `column_meta.pkl` | Column similarity (primary signal) | ✅ | ✅ |
| `row_label_index.faiss` + `row_label_meta.pkl` | Row-label value similarity (primary signal) | ✅ | ✅ |
| `bm25_table_index.pkl` | Lexical signal | ✅ | ✅ |
| `qa_pairs.json` | Verified question → table examples (strong-match pin) | ✅ | ✅ (`qa_pairs: 0` per build stamp) |
| `qa_index.faiss` + `qa_meta.pkl` | Embedding prefilter ahead of the QA difflib scan | ✅ | ❌ absent — degrades to full scan |
| `schema.json` | Table/column descriptions; UI column labels; SQL prompt | ✅ | ✅ |
| `description_samples.json` | Row-label samples for prompts | ✅ | ✅ |
| `build_stamp.json` | Provenance (returns, tables, source path) | ❌ | ✅ |

The 6.0 index covers **4 QCB returns / 10 tables** (F010, F013, F014, F015), per its build stamp. The two indexes describe different databases and share no tables. Serving one host from the other's index **does not fail loudly**: retrieval returns the closest wrong table. `GET /variance/nlp-health` reports which folder is in use and whether every file is present.

---

## 3 · Common Runtime Request Flow

### Auth / user / tenant / version context

```
Query string ?loginId=…&tenantId=…         (tenantId sent in both modes, empty under 5.5)
  ▼
main.request_context_middleware            request id (honours inbound X-Request-ID),
                                           login/tenant into log ContextVars        [main.py:73]
  ▼
auth/deps.require_login → RequestContext(login_id, tenant_id)  frozen, stripped    [config/context.py]
  ├─ profile.validate_context(ctx)         unknown / unprovisioned tenant → 400 (HostProfileError)
  └─ auth/service.get_allowed_form_ids(ctx)
        login ──(user master)──► DepartmentId ──(dept master)──► allowed return ids
        TTL-cached by (tenant_id, login_id), AUTH_TTL_SEC=3600                   [auth/service.py:92]
  ▼
auth/deps.require_return_access(ctx, return_id)   before any compute            [auth/deps.py:126]
```

- **No JWT signature verification** happens in this backend. Under 6.0 the frontend decodes the JWT to extract the login, and the backend trusts `loginId`/`tenantId` from the query string. See §10.
- `DV_AUTH_ENABLED=false` bypasses `require_login`/`require_return_access` and the retriever's auth filter. It is announced **once per process** at WARNING ([auth/deps.py:34](../backend/auth/deps.py#L34)). Under 6.0 with auth off, `DV_DEV_TENANT_ID` supplies the tenant.
- **Version is per-process, not per-request.** `VERSION` is read once at import ([settings.py:72](../backend/config/settings.py#L72)). To serve both hosts, run two processes, each with its own `.env` or a `DV_ENV_FILE` overlay ([settings.py:42-44](../backend/config/settings.py#L42-L44)).

### Request routing — is there an intent classifier?

No free-text router. The **UI** chooses the path: the wizard controls call the manual routes, and the NL bar calls `/variance/nlresolve`. Inside `nlresolve` there is a staged, mostly deterministic cascade in which the LLM is used for one step only:

```
POST /variance/nlresolve                                                          [api/nlp.py:387]
  STAGE 1  query_analyzer.analyze_query()   named return? date phrase? dom/overseas scope?
                                            leaves metric_text = the words describing the data
  GUARD    names a return with no embeddings → refuse with a clarification        [:464]
  GUARD    names >1 return variant (e.g. ALE Domestic vs Overseas) → ask which    [:574]
  STAGE 2  retriever.get_relevant_schema()  FAISS + BM25 fused, FILTERED to allowed returns
  GATE     confidence < ASK_FLOOR (0.35)     → ask "which return?"
           confidence < AUTO_PROCEED (0.60)
              or a tie (TIE_EPSILON 0.08)    → ask "which table?"
  STAGE 3  intent_resolver.resolve_intent() LLM picks return/table/columns from the shortlist ONLY;
                                            any failure → deterministic fallback, never a 404
  GATE     require_return_access() on the resolved return                          [:677]
  STAGE 4  date_resolver.resolve_reporting_date()  phrase → real dates present in the table
  STAGE 5  service.compute_variance()        the SAME call as /variance/compute
```

Clarifications are **stateless**. The response carries `resolved_context`, and the client echoes it back unchanged with `dimension` + `clarification_answer`. `"__skip__"` means "stop asking, use your best guess".

---

## 4 · Functional Flows

### Find a return
| | |
|---|---|
| **Purpose** | Search the returns master by name; resolve to one return plus its tables, or a scored candidate list. |
| **Entry point** | `GET /variance/find` [`api/variance.py:40`] → `data/service.find_return_and_tables` → `data/report_lookup.py` |
| **Data sources** | Returns.xml / Return.xml, NonXBRLReturns.xml, TableMapping.xml, XML_Query.xml fallback |
| **AI model** | None |
| **Note** | Results are **not** filtered to allowed returns, by design: a user can see that a return exists. Authorization is enforced at compute time. `has_mapping` lets the UI grey out unusable candidates. |

### Discover available dates
| | |
|---|---|
| **Purpose** | Populate the Reporting Date picker with dates that actually hold data. |
| **Entry point** | `GET /variance/dates` [`api/variance.py:171`] |
| **Processing** | `require_return_access`, then a DISTINCT on the filter column (usually `RDATE`), newest first, frequency-filtered |
| **AI model** | None |

### Compute variance — the core feature
| | |
|---|---|
| **Purpose** | Compare every numeric cell in a table between a reporting date and up to 3 earlier periods. |
| **Entry point** | `POST /variance/compute` [`api/variance.py:87`] → `data/service.compute_variance` → `data/calculate_variance.calculate_variance` [`:485`] |
| **Inputs** | `return_id`, `table_name`, `reporting_date` (`DD-MON-YYYY`), `reporting_period` (1–12) **or** `comparison_dates` (≤3, replaces `reporting_period`), `comparison_mode` (`vs_current` \| `sequential`), `selected_columns` |
| **Processing** | 100% deterministic. Resolve physical table → previous dates from `RepFreq` (`get_previous_dates` [`:224`], frequency codes M/Q/H/C/A/B/W/F/D) → fetch both periods → match rows by business identifier from `PrimaryColumn` (not row order) → per-column absolute and % change using `Decimal` |
| **Row matching** | Serial columns (`SRNO`, `SLNO`, `SNO`, `ROWNUM`) are excluded from identity and comparison. Rows present in only one period are still reported. |
| **Output** | Pre-formatted cells: `change`, `pct_change`, `variance_summary` with `color` (`success`/`danger`/`""`) and `arrow` (▲/▼). The renderer does no arithmetic. |
| **AI model** | None — the numbers never pass through a model |
| **Fallback** | No rows for the date → `200 {"error": "No data found…"}` (not an HTTP error); missing periods listed in `missing_periods` |

### Natural-language variance
| | |
|---|---|
| **Purpose** | Ask a question and get a **computed** variance table, rather than a pre-filled form. |
| **Entry point** | `POST /variance/nlresolve` [`api/nlp.py:387`] |
| **Data sources** | Embedding index (required), returns master + table mapping, Oracle |
| **AI model** | `qwen2.5:7b` (intent resolution only) — chooses return/table/columns from an already-authorized shortlist and never writes SQL |
| **Grounding** | `_validate_grounding()` rejects any answer naming a table/column outside the shortlist [`intent_resolver.py:250`]. One corrective retry, then `_resolve_deterministic()` [`:341`]. |
| **Date handling** | `date_intent.py` parses "last quarter", "Q1FY25", "QoQ", "since", ranges. `date_resolver.py` maps these onto real table dates. No date given → latest date with data. Every substitution is reported in `interpretation.date_notes`. |
| **Retry** | If the anchor date returns no rows, the route retries once from the next real date and adds a note saying so [`api/nlp.py:776`]. |
| **Output** | The compute payload + `confidence` + `interpretation` (resolved columns and their human labels from `schema.json`, dates used vs requested). The UI shows these as chips. |

### Free-form NL → SQL
| | |
|---|---|
| **Purpose** | Open-ended questions answered by LLM-generated SQL. |
| **Entry point** | `POST /variance/nlquery` [`api/nlp.py:839`] |
| **AI model** | `hf.co/defog/sqlcoder-7b-2:Q5_K_M` via `sql_generator.generate_sql` [`:599`] |
| **Guard** | `validate_sql` [`:682`]: SELECT-only, banned-keyword list, table/column allowlist taken from the caller's own authorized shortlist — **this allowlist is the authorization boundary** |
| **Status** | **Not called by the shipped frontend.** Reachable API surface with no UI behind it; treat as a pending product decision. |

### Access control
| | |
|---|---|
| **Chain** | login → user master → `DepartmentId` → department master → allowed return ids (identical on both hosts; only file shapes differ) |
| **Enforced** | ① `require_login` ② `require_return_access` before every compute ③ retriever shortlist filtered **before** the LLM sees it |
| **Allow-list endpoint** | `GET /auth/my-returns` returns the resolved allow-list. The frontend loads it on mount, and it is also the first thing to check for an access problem. |
| **Wired, not live** | Role/permission (`can_generate_instance`, `RoleAccess.xml`, `option_id`). 6.0 `OptionId` values are unconfirmed, so `option_id()` returns `None` and must be read as *cannot determine*, never *denied*. |

### Diagnostics
`GET /health` (profile, version, resolved paths under 5.5) · `GET /app-config` (the version switch, read by the frontend) · `GET /variance/nlp-health[?check_model=true]` (index folder, file presence, NLP package imports, optional model load) · `logs/ai/<date>.jsonl` (one record per LLM call).

---

## 5 · Complete AI Model Inventory

"Local" = loaded in-process. "Remote Ollama" = HTTP call to the shared self-hosted proxy at `DV_NLP_OLLAMA_URL`. All names are defaults from [backend/nlp/nlp_config.py](../backend/nlp/nlp_config.py) and can be changed by env var without a code change.

| Model | Type | Used in | Input | Output | Config variable |
|---|---|---|---|---|---|
| `BAAI/bge-large-en` (~1.3 GB) | Local SentenceTransformer | Retrieval — embeds the query's `metric_text` (and the full question for the QA prefilter) | Query text + BGE prefix `"Represent this sentence for searching relevant passages: "` | Dense vector for FAISS | `DV_NLP_EMBED_MODEL`, `DV_NLP_QUERY_PREFIX` |
| `qwen2.5:7b` | Remote Ollama | `nlresolve` intent resolution | Question + authorized shortlist (≤40 columns per table) + one few-shot example | ~100-char JSON `{return_id, table_name, selected_columns}`; `num_predict=200`, 20 s timeout | `DV_NLP_INTENT_MODEL` |
| `hf.co/defog/sqlcoder-7b-2:Q5_K_M` | Remote Ollama | `nlquery` SQL generation | Prompt with schema context, row-label samples, resolved time context | One Oracle SELECT; `temperature=0`, 60 s timeout | `DV_NLP_OLLAMA_MODEL` |

**Why two LLMs:** a SQL-completion model cannot reliably obey "respond with JSON only". In production it produced two ungrounded/empty answers (~50 s wasted) before a 404. Intent resolution therefore uses a general instruction model ([nlp_config.py:221-235](../backend/nlp/nlp_config.py#L221-L235)).

**Why intent resolution does not write SQL:** `db.execute_query()` runs SQL without parameterization. Letting a model author free SQL against the live connection would be an injection surface. On the NL path the model only picks from an authorized list, and the actual SQL comes from the unmodified `build_query()`.

**`MODEL_PROFILES`** ([nlp_config.py:275](../backend/nlp/nlp_config.py#L275)) sets prompt style, temperature and `num_predict` for `qwen2.5:7b`, `llama3.1:latest` and `sqlcoder-7b-2`. Switching `DV_NLP_OLLAMA_MODEL` picks up the matching profile.

> **The embedding model must match whatever built the index.** A mismatched model produces a different vector space, and similarity scores become meaningless without any error being raised. Confirm against the external build tool before changing `DV_NLP_EMBED_MODEL`.

---

## 6 · NLP Layer — Failure Handling Reference

| Failure | Behaviour |
|---|---|
| NLP packages missing (faiss / sentence-transformers / rank-bm25) | Only the NL routes fail; manual routes keep working, because NLP imports are function-local. The error names the missing package and points to `/variance/nlp-health`. |
| Index folder for the active `VERSION` missing | `index_dir_problem()` reports one clear line ("No embedding folder for VERSION=…") rather than five "file MISSING" lines |
| BM25 / QA / qa_index file missing | That signal silently contributes nothing |
| Query names a return that is not indexed | Refuses with a non-skippable clarification rather than answering from a different return |
| Low retrieval confidence | Clarification ("which return?" / "which table?") instead of a guess |
| Ollama unreachable / timeout (intent) | Instant deterministic fallback; circuit breaker skips the LLM for `DV_NLP_INTENT_FAILURE_COOLDOWN_SEC=60` s |
| LLM answer ungrounded / malformed | One shorter corrective retry, then deterministic fallback; audited with `fell_back_to_deterministic=true` |
| SQL invalid (`nlquery`) | Retry once; still invalid → `422` with the validation warnings; recorded in the AI audit log |
| Anchor date has no rows | One retry from the next real date, with a note |
| Resolved table not in `TableMapping.xml` | Proceeds via the `XML_Query.xml` fallback (not an error) |
| AI audit write fails | Swallowed after one warning — never fails the request |

---

## 7 · Frontend & API Flow

React 18 + Vite SPA ([frontend/src](../frontend/src)), no Redux. `LayoutContainer.jsx` owns all wizard state. The other components are presentational: `ControlBar` (return/table/date/period controls + NL bar), `TablePanel` + `DataVarianceTable.jsx` (result grid), `VisualizationPanel` (Recharts), `ColumnVisibilityMenu`, `NoticeToast`. Layout uses `react-resizable-panels`.

**Identity bootstrap** ([src/auth/index.js](../frontend/src/auth/index.js)): version resolution order is ① `VITE_APP_VERSION` build pin → ② `GET /app-config` → ③ URL shape (`_at`/`_lid` ⇒ 6.0, bare `loginId` ⇒ 5.5). Step ③ is a logged, risky guess. A wrong guess against 6.0 means every request 401s with "tenantId required". The 6.0 strategy strips `_at` from the address bar into `sessionStorage` before any request goes out, so the token does not leak in the `Referer` header.

| Functionality | Endpoint | Frontend trigger |
|---|---|---|
| Version / auth strategy | `GET /app-config` | app start |
| Search return | `GET /variance/find` | Return field |
| Date list | `GET /variance/dates` | table selected |
| Compute | `POST /variance/compute` | Compute button |
| NL question | `POST /variance/nlresolve` | NL bar submit / clarification answer |
| Allowed returns | `GET /auth/my-returns` | `LayoutContainer` on mount |

Every call goes through `request()` in [src/api.js](../frontend/src/api.js), prefixed with `API_BASE_URL`, with `loginId`/`tenantId` appended. A 200 response with a non-JSON body is reported as "the server returned a non-JSON response". That almost always means IIS served `index.html` instead of forwarding to the API (see §14).

---

## 8 · Deployment & Configuration Flow

```
Browser ── IIS site
            ├─ /DataVar/      ← frontend dist from `npm run build:55`
            │     web.config: ^api/(.*) | ^DataVar/api/(.*)       → http://127.0.0.1:8004/{R:1}
            └─ /DataVar6.0/   ← frontend dist from `npm run build:60`
                  web.config (renamed from web.60.config):
                              ^api/(.*) | ^DataVar6\.0/api/(.*)  → http://127.0.0.1:8003/{R:1}
                                         │   (port hardcoded in web.config — must match
                                         │    DV_SERVER_PORT_55/60 by hand; no automated check)
                                         ▼
            python dev_server.py  (VERSION=5.5 → :8004)     python dev_server.py  (VERSION=6.0 → :8003)
            separate folder / .env or DV_ENV_FILE overlay   separate folder / .env or DV_ENV_FILE overlay
                                         ▼
                        Oracle (CRILC | IDEALCRILC) · XML repo · Ollama proxy
```

- Direct-on-Windows: no Docker, no service wrapper in the repo. `dev_server.py` is the launcher (`DEV_SERVER_RELOAD=1` enables autoreload). Running it as a Windows service (NSSM, Task Scheduler) is an ops concern and **needs verification** for the production box.
- Requires IIS **URL Rewrite** + **ARR** with proxy enabled.
- The frontend bakes in only the deploy path (`VITE_BASE_PATH`, `VITE_API_BASE_URL`) at build time, so there are two builds. Auth behaviour is still chosen at runtime from `/app-config`.
- `npm run build` **is the 6.0 build**. Use `build:55` for 5.5.
- Every build copies **both** `web.config` (5.5, port 8004) and `web.60.config` (6.0, port 8003) into `dist/`. For 6.0, delete `web.config` and rename `web.60.config` to `web.config`.

### Configuration reference (names and purpose only — no secret values)

Version-keyed settings follow `DV_<NAME>_55` / `DV_<NAME>_60` → unsuffixed `DV_<NAME>` → default ([settings.py:96-107](../backend/config/settings.py#L96-L107)).

| Variable | Purpose | Secret? |
|---|---|---|
| `VERSION` (aliases `DV_APP_VERSION`, `APP_VERSION`) | 5.5 vs 6.0 — moves DB, repo, port, API prefix, CORS and NLP index together | No |
| `DV_ENV_FILE` (real OS env var) | Overlay `.env` for running a second instance | No |
| `DV_BASE_PATH_55/_60`, `DV_BASE_PATH` | Repository roots | No |
| `DV_DB_HOST/PORT/SERVICE/USER/PASSWORD_55/_60` | Oracle connection per version | **Yes — password** |
| `DV_DB_MAX_ROWS`, `DV_DB_POOL_MIN/MAX`, `DV_DB_CALL_TIMEOUT_MS` | DB limits | No |
| `DV_IS_SP_TABLE_DATA_ENABLED_55/_60`, `DV_DP_SCHEMA_55/_60` | `_DP` suffix rule and schema prefix | No |
| `DV_SERVER_HOST`, `DV_SERVER_PORT_55/_60` | Bind address (default `0.0.0.0`) and port (8004 / 8003) | No |
| `DV_API_BASE_PATH_55/_60` | FastAPI `root_path` (affects `/docs` URLs only) — `/DataVar/api`, `/DataVar6.0/api` | No |
| `DV_CORS_ORIGINS_55/_60` | CORS allow-list — `scheme://host[:port]` only, never a path | No |
| `DV_AUTH_ENABLED`, `AUTH_TTL_SEC`, `DV_DEV_TENANT_ID` | Auth toggle, cache TTL, dev tenant | No |
| `DV_*_PATH` overrides (`DV_RETURNS_XML_PATH`, `DV_XML_USER_PATH`, …) | Pin individual repository files | No |
| `DV_FISCAL_YEAR_START_MONTH` | Override the profile's FY start | No |
| `DV_NLP_OLLAMA_URL`, `DV_NLP_INTENT_MODEL`, `DV_NLP_OLLAMA_MODEL`, timeouts | LLM endpoint and models | URL only |
| `DV_NLP_EMBED_MODEL`, `DV_NLP_INDEX_DIR[_55/_60]` | Embedding model and index folder | No |
| `DV_NLP_CONFIDENCE_AUTO_PROCEED/ASK_FLOOR`, `DV_NLP_RRF_K`, `DV_NLP_SCOPED_RETRIEVAL` | Retrieval tuning and kill switch | No |
| `DV_LOG_LEVEL`, `DV_LOG_DIR`, `DV_AI_LOG_DIR`, `DV_LOG_RETENTION_DAYS` | Logging | No |
| `DOTNET_API_URL` | .NET host API base | No |
| Frontend: `VITE_API_BASE_URL`, `VITE_BASE_PATH`, `VITE_PROXY_TARGET`, `VITE_APP_VERSION` | Build-time deploy path; dev proxy target; optional version pin | No |

> **⚠ Secrets.** `.env` holds live Oracle passwords in plaintext. It is gitignored (`.gitignore:2`), but it is copied by hand to servers and has been shared in chat. Rotate the passwords and restrict file ACLs on the servers. `.env.example` and `config/env/*.example` are the placeholder templates to keep in git.

---

## 9 · Logging, Debugging & Testing

Configured in [backend/logging_config.py](../backend/logging_config.py). Full detail is in [logging.md](logging.md).

| Stream | Destination | Contents |
|---|---|---|
| Console | stdout | Everything |
| Daily log | `logs/<YYYY-MM-DD>.log` | Everything, including uvicorn access lines |
| AI audit | `logs/ai/<YYYY-MM-DD>.jsonl` | One record per LLM call: query, model, prompt style, latency, generated SQL, validation verdict, grounding/fallback |

Line format: `timestamp | LEVEL | request-id | logger | message`. The request id is injected by a logging filter, and an inbound `X-Request-ID` header is honoured and echoed back, so one request can be traced with `grep ' <id> ' logs/<date>.log`. Files roll at midnight and are pruned after `DV_LOG_RETENTION_DAYS` (30).

> **`DV_LOG_LEVEL=DEBUG` writes per-row regulatory cell values to disk.** Treat it as a deliberate, time-boxed action. Even at INFO, logs contain `login_id` (an email under 6.0), full query text and generated SQL.

**Testing:** pytest under [tests/](../tests): `test_data_layer.py`, plus `tests/nlp/` (`test_date_intent`, `test_date_resolver`, `test_index_dir_version`, `test_cache_invalidation`). Offline evaluation scripts are in [scripts/](../scripts): `eval_retrieval.py` and `eval_scoped_retrieval.py` (the 60-query retrieval benchmark behind `RRF_K=5` and `AUTO_PROCEED=0.60`), `eval_nlp_dates.py` (date scenario matrix against real data). `build_excel_tablemapping.py` is a helper that generates table mappings.

---

## 10 · Security Flow

### Confirmed protections
- Authorization applied **before** the LLM: the retriever shortlist is filtered to allowed returns, and every LLM answer is validated against that shortlist
- `require_return_access` runs on every compute path, including client-supplied clarification answers and the re-derived return id in `nlresolve`
- The table-mapping file is always located from the return's own `TblPath`, never from the `table_mapping_path` the client echoes back
- SQL on the manual and NL-variance paths is built by `build_query()`. Identifiers are checked by `is_safe_identifier` and literals escaped by `sql_literal` ([calculate_variance.py:22-37](../backend/data/calculate_variance.py#L22-L37))
- `nlquery` SQL: SELECT-only, banned keywords/patterns, table/column allowlist
- Read-only by design: no write path to Oracle or the repository
- Unknown tenant → 400 (distinguishable from a 403 for a known-but-disallowed caller)
- Auth cache keyed by `(tenant_id, login_id)` — no cross-tenant bleed

### Gaps to flag for handover
- **No JWT verification in the backend.** `loginId`/`tenantId` are trusted from the query string. Anyone who can reach the port can claim any login. The mitigation is network placement (loopback plus IIS), and the current default bind is `0.0.0.0`: set `DV_SERVER_HOST=127.0.0.1` on servers.
- `DV_AUTH_ENABLED=false` disables all access checks. The current server `.env` has it set to `false`, and it must be `true` in production.
- `/auth/my-returns` exposes a user's entitlements. The code comments call for restricting it in production, but the shipped frontend calls it on mount (`LayoutContainer.jsx:266`), so it cannot simply be removed.
- `/variance/nlquery` is reachable without a UI and runs LLM-authored SQL guarded by a regex validator, not a SQL parser.
- No rate limiting anywhere.
- Plaintext DB credentials in server `.env` files (see §8).

---

## 11 · Reference Matrices

### Data source → storage → consumer

| Source | Stored where | Consumed by |
|---|---|---|
| Returns master (XBRL + non-XBRL) | iDEAL repo, version-specific path | Find, NL return lookup, date resolution (`RepFreq`) |
| TableMapping.xml / XML_Query.xml | iDEAL repo, per return | Find, compute (identifier columns, filter column) |
| User / department masters | iDEAL repo (per tenant under 6.0) | Authorization |
| Tenant registry / period master | 6.0 repo | Tenant validation, frequency fill-in |
| Oracle regulatory tables | CRILC / IDEALCRILC | Dates, compute, `nlquery` |
| Embedding index | `artifacts/nlp-index/ideal-55|ideal-60` (built externally) | NL retrieval, column labels, SQL prompt context |
| Ollama models | Remote proxy | Intent resolution, SQL generation |

### Functionality → data → model

| Functionality | Required data | AI model | Output |
|---|---|---|---|
| Find return | Returns master, TableMapping | None | return + tables, or candidates |
| Available dates | Oracle | None | date list |
| Compute variance | Oracle, TableMapping, `RepFreq` | None | variance rows |
| NL variance | Index, XML, Oracle | bge-large-en + qwen2.5:7b (picks only) | variance rows + interpretation, or clarification |
| NL → SQL | Index, Oracle | bge-large-en + sqlcoder-7b-2 | sql + columns + rows |
| Access control | User/dept masters | None | allowed return ids |

---

## 12 · Worked End-to-End Examples

### A. Manual compute (5.5)

```
Host opens /DataVar/?loginId=iris810
  → GET /app-config → {"version":"5.5"} → strategy55 reads loginId
  → user types "RAQ" → GET /variance/find?return_name=RAQ → candidates → user picks
  → GET /variance/dates?return_id=2041&table_name=CIMS_RAQ_Q_SEC1_PART_C_O → date list
  → POST /variance/compute {reporting_date:"31-MAR-2025", reporting_period:1, comparison_mode:"vs_current"}
      → require_return_access → CRILC.CIMS_RAQ_Q_SEC1_PART_C_O[_DP]
      → previous quarter from RepFreq=Q → fetch both → match on PrimaryColumn → Decimal diffs
  → TablePanel renders pre-formatted cells; VisualizationPanel charts them
```

### B. Natural-language question

```
"show the variance in period delinquency for RAQ last quarter"
  → analyze_query: return=RAQ, date="last quarter", metric_text="period delinquency"
  → retriever: column + row-label + table + BM25 signals fused (RRF k=5), filtered to allowed returns
  → confidence ≥ 0.60, no tie → proceed
  → qwen2.5:7b → {"return_id":"2041","table_name":"CIMS_RAQ_Q_SEC1_PART_C_O",
                  "selected_columns":["PERIOD_DELINQUENCY"]} → grounded ✔
  → require_return_access(2041)
  → date_resolver: "last quarter" → 31-DEC-2024 (a real date in the table) + comparison date
  → compute_variance (same as A) → result restricted to PERIOD_DELINQUENCY
  → UI chips: "Period Delinquency", dates used, any date_notes
```

### C. Low confidence → clarification

```
"exposure by country"
  → table_confidence 0.48 (< 0.60) → {"needs_clarification":true,"dimension":"table",
       "options":[…], "resolved_context":{…}}
  → user picks an option → POST again with dimension="table", clarification_answer=<id>,
       resolved_context echoed → scoped shortlist → intent → dates → compute
```

---

## 13 · Production Readiness

| Area | Status | Note |
|---|---|---|
| Manual variance (find/dates/compute) | ✅ Implemented | Deterministic; in production on 5.5 |
| NL variance (`nlresolve`) | ✅ Implemented | Grounded LLM pick + deterministic fallback + clarifications |
| iDEAL 6.0 integration | ✅ Implemented | Tenant-scoped paths; role `OptionId` mapping still open ([integration-plan.md](integration-plan.md) §6) |
| Two versions on one server | ⚠ Operational care | Needs two processes with separate `.env`/overlay and two IIS dists; ports hardcoded in `web.config` |
| JWT verification | 🔴 Not implemented | Identity trusted from the query string |
| Auth enabled on servers | 🔴 Check | Server `.env` currently has `DV_AUTH_ENABLED=false` |
| Secrets management | 🔴 Not production-ready | Plaintext `.env` on servers |
| Rate limiting | 🔴 Not found | — |
| Response schemas | ⚠ Partial | No `response_model`; [models.md](models.md) is the contract |
| Embedding build pipeline | ❓ Needs verification | External tool; 6.0 index covers 4 returns, 0 QA pairs |
| `nlquery` product decision | ❓ Open | API exists, no UI |
| Windows service wrapper | ❓ Needs verification | Not in repo |

---

## 14 · Known Limitations & Troubleshooting

### Known limitations
- **NL coverage = index coverage.** A return that exists but was never indexed cannot be answered by NL search. The UI says so rather than answering from another return.
- **One process per version.** Changing `VERSION`, or the index folder, requires a restart.
- **LLM latency.** Intent calls are capped at 20 s, with a deterministic fallback. SQL generation can take 60 s or more on the shared proxy (a 61 s attempt was recorded in the audit log).
- **Comparison periods are capped at 3**, because the table renders one column group per period.

### Troubleshooting playbooks

**Requests go to the wrong backend port / the wrong version answers**
1. `curl http://127.0.0.1:8004/app-config` should return 5.5, and `:8003` should return 6.0. If not, the backend `.env`/`VERSION` is wrong. Two processes sharing one `.env` both claim the same port.
2. `curl http://localhost/DataVar/api/app-config` should return 5.5, and `/DataVar6.0/api/...` should return 6.0. If step 1 passes but this fails, the problem is IIS.
3. Check the deployed `web.config` in each vdir (8004 vs 8003), and check `appcmd list config -section:system.webServer/rewrite/globalRules` for a server-wide rule overriding both.
4. In DevTools → Network, check the request URL prefix (`/DataVar/api` vs `/DataVar6.0/api`). A wrong prefix means the wrong dist is deployed (`build:55` vs `build:60`).

**`[Errno 10048]` port already in use on start**
Another process holds the port. Usually it's an earlier run that is still alive. Find it with `Get-NetTCPConnection -LocalPort 8004 -State Listen`, stop it, then start once.

**"server returned a non-JSON response" / login or tenant 401s**
IIS served `index.html`: no rewrite rule matched the prefix. Check the vdir name and `web.config`. If `/app-config` is unreachable, the frontend guesses the version from the URL, and against 6.0 that produces "tenantId required" 401s.

**CORS errors**
`DV_CORS_ORIGINS_*` entries must be origins (`http://host[:port]`) with no path. Add the exact hostname or IP users type in the browser.

**NL questions fail or answer from the wrong table**
1. `GET /variance/nlp-health` — check the folder matches `VERSION`, all files are present and packages import.
2. Check `logs/ai/<date>.jsonl` for `grounded:false` / `fell_back_to_deterministic:true`, or rejected SQL.
3. Confirm the return is covered by the index (`build_stamp.json` lists the returns for 6.0).

**Every query 500s with ORA-00942**
`VERSION` points at one host but the DB/schema settings point at the other. Check `DV_DB_*_55/_60` and `DV_DP_SCHEMA_*`.

**Every request 403s for a valid user**
Check `GET /auth/my-returns`. An empty allow-list usually means a wrong master file or delimiter (`|` for 5.5, `,` for 6.0), or the login is not in the user master.

---

*Compiled from a direct source trace of `data-variance-module-` (branch `6-host-profile-config`) and the existing `docs/` set. Items marked "needs verification" are explicit gaps. Close them against current source and the production servers before sign-off.*
