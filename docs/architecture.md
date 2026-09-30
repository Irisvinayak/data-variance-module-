# Architecture

Written for developers working on this repository.

## What this module is

A FastAPI backend plus a React SPA that computes **period-over-period variance**
for iDEAL regulatory returns, and answers natural-language questions about the
same data.

It is not a standalone product. It is embedded in an existing iDEAL host
application, which supplies the caller's identity and owns the repository of
XML metadata and the Oracle database this module reads.

## The one thing to understand first: two hosts, one codebase

The same code serves two different iDEAL applications:

| | iDEAL 5.5 | iDEAL 6.0 |
|---|---|---|
| Host app | ASP.NET MVC | React + .NET Web API |
| Tenancy | none — one repository | multi-tenant |
| Repository root | `D:\Repo5.5` | `D:\Repo6\<TenantId>` |
| Identity arrives as | `?loginId=` (a username) | a JWT in `?_at=` (an email) |
| Returns master | `Database\Returns.xml` | `<tenant>\DataBase\Return.xml` |
| Return element | `<Return>` | `<Row>` |
| Allowed-returns attr | `Forms="a|b|c"` | `ReturnId="a,b,c"` |
| Oracle schema | `CRILC` | `IDEALCRILC` |
| Default port | 8004 | 8003 |

`VERSION` in the root `.env` is the **single switch**. It selects the DB, the
repository root, the port, the reverse-proxy prefix, the CORS origins and the
embedding index together — see [backend/config/settings.py](../backend/config/settings.py),
which explains why each is version-keyed rather than set once.

**Every host difference is confined to [backend/hosts/](../backend/hosts/).**
`HostProfile` (in `base.py`) is an abstract class answering exactly two kinds of
question — *where is file X for this request?* and *what shape is that file?* —
with `Ideal55Profile` and `Ideal60Profile` implementing them. Everything else
(the variance engine, the SQL layer, the NLP retriever, the React UI) is
host-agnostic and must stay that way.

> If you find yourself writing `if is_legacy_mode():` outside `backend/hosts/`,
> the difference belongs in a profile instead.

The differences are not cosmetic. 5.5 separates allowed return ids with `|` and
6.0 with `,`; splitting on the wrong one does not raise, it yields one nonsense
token that matches no return, so every user resolves to an empty allow-list and
every request 403s. [docs/integration-plan.md](integration-plan.md) §5 records
that and four sibling defects found by inspecting the real repositories.

## Request context

`RequestContext` ([backend/config/context.py](../backend/config/context.py)) is
a frozen dataclass carrying `login_id` and `tenant_id`. It is threaded through
the data layer instead of a bare `tenant_id` string, so the next host
difference does not mean re-threading a dozen signatures.

`ANONYMOUS` is the context for code with no request identity — startup
diagnostics, `/health`, and offline scripts under `scripts/`. Under 5.5 it
resolves like any request (paths do not depend on identity there); under 6.0
the profile rejects it, which is correct — there is no tenant-free lookup.

## Layout

```
backend/
  api/          HTTP layer — one module per area, mounted by main.py
    meta.py       /health  /app-config  /variance/nlp-health
    variance.py   /variance/find  /compute  /dates
    nlp.py        /variance/nlresolve  /nlquery  + their private helpers
    auth.py       /auth/my-returns
    errors.py     the shared domain-exception -> HTTP mapping
  auth/         login -> department -> allowed return ids
  config/       settings (env-derived) + RequestContext
  data/         the variance engine, Oracle access, XML lookups
  hosts/        HostProfile per iDEAL version   <- the only host-aware package
  nlp/          NL query -> table/column shortlist -> intent or SQL
  ai_audit.py   structured LLM audit trail
  logging_config.py
  main.py       app object, middleware, exception handlers, router mounting
frontend/src/
  auth/         host-aware identity resolution (5.5 query param vs 6.0 JWT)
  components/   ControlBar, TablePanel, VisualizationPanel, ...
  api.js        HTTP helpers; every call carries loginId + tenantId
artifacts/nlp-index/   FAISS + BM25 indexes, built externally, read-only here
```

`backend/main.py` owns only what an `APIRouter` cannot carry: the app object, the CORS
and request-correlation middleware, and the app-level exception handlers.

## Request flow: the manual path

```
Browser
  │  GET /variance/find?return_name=…&loginId=…&tenantId=…
  ▼
api/variance.py
  │  Depends(require_login)  ──► auth/deps.py
  │                              ├─ profile.validate_context(ctx)   (unknown tenant -> 400)
  │                              └─ auth/service.get_allowed_form_ids(ctx)
  ▼
data/service.find_return_and_tables(ctx)
  │  ├─ report_lookup.parse_returns(ctx)      Returns.xml / Return.xml
  │  └─ _load_table_mapping(...)              <returnId>\TableMapping.xml
  ▼
  {return_id, table_mapping_path, tables:[…]}       exact match
  {candidates:[…]}                                   fuzzy match

  │  POST /variance/compute   {return_id, table_name, reporting_date, …}
  ▼
data/service.compute_variance(ctx)
  │  ├─ require_return_access(ctx, return_id)          authorization
  │  ├─ resolve_physical_table_name()                  adds schema + _DP suffix
  │  ├─ data/db.execute_query()                        Oracle, SELECT only
  │  └─ data/calculate_variance.calculate_variance()   the arithmetic
  ▼
  {table_name, reporting_date, comparison_periods, columns, rows:[…], …}
```

## Request flow: the natural-language path

`POST /variance/nlresolve` resolves a question **and computes the result in one
round trip**, so the NL bar produces a real table rather than a form to fill in.

```
query
  ▼ nlp/query_analyzer.analyze_query()     normalize; did the query name a return?
  ▼ nlp/retriever.get_relevant_schema()    FAISS + BM25 over the embedding index,
  │                                        then FILTERED to the user's allowed
  │                                        returns — authorization happens HERE,
  │                                        before any model sees a candidate
  ▼ nlp/intent_resolver.resolve_intent()   an LLM picks from that shortlist only;
  │                                        it never writes SQL, and its answer is
  │                                        validated back against the shortlist
  ▼ nlp/date_resolver.resolve_reporting_date()   "last quarter" -> a real date,
  │                                        defaulting to the latest actual submission
  ▼ data/service.compute_variance()        the SAME call /variance/compute makes
```

When confidence is too low the route returns a **clarification** instead of
guessing — see [models.md](models.md#clarification-responses). The follow-up
request echoes `resolved_context` straight back, so there is no server-side
session store.

`POST /variance/nlquery` is the other, more permissive path: an LLM
(`backend/nlp/sql_generator.py`) writes actual SQL, which is then validated against the
authorized table/column allowlist before execution. Note this route is **not
called by the shipped frontend**.

### Why two different LLM paths

`intent_resolver` deliberately does *not* ask for SQL. `backend/data/db.py` executes
SQL with no parameterization, so letting a model author free-form SQL against
the live connection is an injection surface. The intent path keeps the model
choosing from an already-authorized list; the real SQL is then produced by the
existing, unmodified `calculate_variance.build_query()`.

`nlquery` accepts that risk in exchange for open-ended questions, and contains
it with `sql_generator.validate_sql()`: SELECT-only, a banned-keyword check, and
a table/column allowlist derived from the caller's own shortlist. **That
allowlist is the authorization boundary for whatever SQL actually runs.**

The two paths also use different models — a SQL-completion specialist
(`sqlcoder`) cannot reliably follow "respond with JSON only", so intent
resolution uses a general instruction model (`qwen2.5`).

## Where authorization is enforced

Three places, deliberately layered:

1. `auth/deps.require_login` — resolves the login to a set of allowed return
   ids, and rejects an unknown tenant or unknown user.
2. `auth/deps.require_return_access` — checked before any compute: by
   `/variance/dates` and `/variance/compute`, and by `/variance/nlresolve` on
   every client-supplied return/table clarification answer and again on the
   resolved return before it computes. The table-mapping file is always located
   from the return's own `TblPath` in the returns master, never from the
   `table_mapping_path` the client echoes back.
3. `nlp/retriever` — the candidate shortlist is filtered to allowed returns
   **before** the LLM sees it, so a model cannot name a table the user may not
   read. `sql_generator.validate_sql` then re-checks the generated SQL against
   that same shortlist.

`DV_AUTH_ENABLED=false` bypasses 1 and 2 for local development. It is announced
once per process at WARNING and must never be false in production.

## The embedding index

`artifacts/nlp-index/ideal-55/` and `ideal-60/` hold FAISS and BM25 indexes
built by an **external** tool and dropped in wholesale; this project only reads
them. They cover different databases (5.5's CIMS returns, 6.0's QCB returns) and
share no tables, so serving one host from the other's index does not fail
loudly — retrieval returns the closest wrong table. `backend/nlp/nlp_config.py` keys the
folder off `VERSION` for that reason, and `GET /variance/nlp-health` reports
which folder is in use and whether every file is present.

Index records carry only `{text, table, column}` — no `return_id`. The mapping
from table to return is resolved live against this app's own XML
(`backend/nlp/return_lookup.py`), so the index can be rebuilt by any tool without ever
touching authorization.

## Startup cost, and why NLP imports are function-local

The embedding model is ~1.3 GB. Every `from ..nlp...` import inside an
`backend/api/nlp.py` or `backend/api/meta.py` handler is **function-local on purpose**, so a
deployment that only uses the manual routes never loads FAISS or
sentence-transformers.

Hoisting them to module scope would make `include_router()` import the NLP stack
at startup, and a box without those packages would fail to boot instead of
degrading to "the NLP routes 500, everything else works". There is a check for
this in the router split's commit message; if you add an NLP import, keep it
inside the function.

## Concurrency model

Route handlers are `def`, **not** `async def`, so FastAPI runs them in its
threadpool. This is deliberate: every route does blocking work (synchronous
`oracledb`, `requests` to Ollama, FAISS, network-share file stats). As
`async def` they ran on the event loop, and one slow request froze the whole
process — `/health` measured 30s timeouts behind a single slow compute.

Consequences to keep in mind:

- Module-level caches are shared across threads. The existing ones take a lock;
  if you add one, do the same.
- `backend/data/db.py` sizes its Oracle pool (`DV_DB_POOL_MAX`, default 20) against the
  threadpool. Raising one without the other reintroduces queueing.

## Frontend

One codebase, both hosts. `src/auth/` asks the backend `GET /app-config` for
`VERSION` at startup and picks a strategy — 5.5 reads `?loginId=`, 6.0 decodes
the JWT in `?_at=`. So flipping `VERSION` needs no frontend rebuild.

Only the **deploy path** is baked in at build time (`VITE_BASE_PATH`,
`VITE_API_BASE_URL`), because IIS serves the two hosts from different virtual
directories. Hence two builds: `npm run build:55` and `npm run build:60`, and
two `web.config` files in `public/`.

## See also

- [functionality.md](functionality.md) — what each feature does, in product terms
- [models.md](models.md) — data models and API contracts, with real payloads
- [logging.md](logging.md) — log streams, levels, the AI audit trail
- [integration-plan.md](integration-plan.md) — the historical record of the
  5.5/6.0 integration: verified differences, bugs found, open questions

## NLP date resolution

Both NLP endpoints read date phrases through one parser, `backend/nlp/date_intent.py`, which is pure and never touches the DB. It returns a `DateIntent`:
- the anchor period and any explicit comparison periods
- the relative part (`last N <unit>`) and the XoX part (QoQ / MoM / YoY)
- `since` and a range

Each explicit period is a range with a granularity, so `March 2025` means the whole month, not 1 March.

- **/variance/nlresolve**: `date_resolver.resolve_reporting_date` maps the intent onto the dates the table really has. It uses `service.get_available_dates`, the same list as the manual date dropdown, which is frequency-filtered. It returns the anchor, up to 2 comparison dates (the same limit as the manual route), and `notes` for every substitution. The notes are returned as `interpretation.date_notes` and shown in the UI chips. A date phrase never fails: anything unmatched falls back to a real date, with a note.
- **/variance/nlquery**: `sql_generator._resolve_relative_time` builds the prompt's RESOLVED TIME CONTEXT from the same intent. It is anchored on the newest data date, not today.
- **Fiscal year**: `HostProfile.fiscal_year_start_month` is 4 (Apr–Mar) for 5.5 and 1 (calendar year) for 6.0. Override it with `DV_FISCAL_YEAR_START_MONTH`.
- **Frequency**: `HostProfile.resolve_frequency` fills an unusable `RepFreq` (blank, or `x` in 6.0) from `PeriodId` → the period master, when the returns master is parsed.
- **Tests**: `tests/nlp/test_date_intent.py` and `test_date_resolver.py`. To run the scenario matrix against real data, use `scripts/eval_nlp_dates.py`.
