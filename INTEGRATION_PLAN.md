# Data Variance Module — 5.5 / 6.0 Unified Configuration Plan

Goal: **one codebase, one deployment artifact**, that can host itself inside either
iDEAL 5.5 (ASP.NET MVC, single-tenant) or iDEAL 6.0 (React + .NET Web API,
multi-tenant), selected by configuration — not by maintaining two folder trees.

---

## 1. What actually differs between the two hosts

Everything that matters is concentrated in **two concerns**: where repository XML
lives, and how the caller's identity arrives. The variance engine, SQL layer,
NLP layer and the entire React UI are byte-for-byte the same problem.

### 1.1 Repository / XML layout

Verified on disk against `D:\Repo5.5` and `D:\Repo6` on 2026-09-09.

| Concern | 5.5 — `D:\Repo5.5` | 6.0 — `D:\Repo6` |
|---|---|---|
| Tenancy | none — one repo per install | `<Base>\<TenantId>\` |
| DB folder name | `Database\` | `DataBase\` (capital B) |
| Tenant registry | — | `<Base>\XML_Tenant.xml` (`TenantId`, `Status`) |
| Returns master | `Database\Returns.xml` | `<tenant>\DataBase\Return.xml` |
| — root element | `<Returns>` / `<Return>` | `<Document>` / `<Row>` |
| Non-XBRL master | `Database\NonXBRLReturns.xml` | `<tenant>\DataBase\NonXBRLReturn.xml` |
| Table mapping root | `Database\<returnId>\` | `<tenant>\DataBase\<returnId>\` |
| `TblPath` shape | `Conf\reports\mpd07\1.0.0\mpd07-table.xml` | `Taxonomy\reports\banking\BR050\br050-table.xml` |
| Instance root | `<Base>\Instance\<returnId>\` | `<tenant>\Instance\<returnId>\` |
| Query XML | `Database\<returnId>\XML_Query.xml` | `<tenant>\DataBase\<returnId>\XML_Query.xml` (some returns use `Query.xml`) |
| User master | `Database\XML_User.xml` | `<tenant>\DataBase\User.xml` |
| Department master | `Database\XML_Dept.xml` | `<tenant>\DataBase\Department.xml` |
| Role access | `Database\XML_RoleAccess.xml` | `<tenant>\DataBase\RoleAccess.xml` (**per-tenant**, not global) |
| Period master | `Database\XML_Period.xml` | `<tenant>\DataBase\Period.xml` |
| Frequency source | `XML_Period.xml@Frequency` / `@EBRFrequency` | `Return.xml@RepFreq` (`D`/`M`/`Q`/`A`/`x`) — `Period.xml` has **no** frequency attrs |

### 1.2 Authentication

| Concern | 5.5 | 6.0 |
|---|---|---|
| Identity transport | `.cshtml` appends `?loginId=iris810&uid=104` to the iframe/redirect URL | .NET API appends `?_at=<JWT>&_lid=<login>`; React stores both in `sessionStorage`, strips them from the URL, then decodes the JWT client-side |
| Claims read | `loginId` query param only | JWT payload `LoginId`, `TenantId` |
| `require_login()` returns | `str` (login_id) | `tuple[str, str]` (login_id, tenant_id) |
| User file | `Database\XML_User.xml` (global) | `<tenant>\Database\user.xml` |
| Department file | `Database\XML_Dept.xml` | `<tenant>\Database\department.xml` |
| User row: login attr | `LoginId` (= `Name`, e.g. `iris810`) | `LoginId` (an email, e.g. `vaibhav@irisindia.net`) |
| User row: dept attr | `DepartmentId` | `DepartmentId` |
| Dept-id attribute | `DeptId` | `Id` |
| Allowed-forms attributes | `Forms` / `NXForms` | `ReturnId` / `NXReturnId` |
| **Allowed-forms delimiter** | **pipe** — `Forms="2001\|2007\|2002"` | **comma** — `ReturnId="2029,4089,4070"` |
| Role-access `OptionId` | **named strings** — `CreateInstance`, `DataVariation`, … | **numeric ids** — `1`, `2`, `3`, … |
| PII in user master | plaintext (`Name`, `EmailId`) | AES-encrypted (`FirstName`, `EmailId`); `LoginId` plaintext |
| Auth cache key | `login_id` | `(tenant_id, login_id)` |
| Return-level check | `is_return_allowed(login, return_id)` | `is_return_allowed(login, tenant, return_id)` |

So the auth difference is **not** "a different algorithm". It is the same
two-hop resolution (`login -> dept -> allowed return ids`) over **differently
named files with differently named attributes, rooted at a different folder**.
That is exactly the shape that belongs behind one interface.

### 1.3 What each branch has that the other lacks

- **`5-final-55-and-60`** (current HEAD, richest): the whole `backend/nlp/`
  package, `period_lookup.py`, `query_xml_lookup.py`, the `backend/output/`
  FAISS/BM25 indices, the reworked React UI, `dev_server.py`. **Zero** tenant
  awareness — auth and all path constants are module-level globals.
- **`2-data-variance-ideal-60`**: full tenant plumbing — `tenant_id` threaded
  through `report_lookup._parse_returns`, `service._load_table_mapping`,
  `find_return_and_tables`, `_get_table_metadata`, `compute_variance` — plus the
  JWT bootstrap in `api.js` and `frontend/public/web.config` for IIS. **No** NLP.
- **`3-intigreating-60-55`** (abandoned mid-work, **not** an ancestor of HEAD):
  first attempt at a `VERSION` switch. It already contains `APP_VERSION`,
  `_normalize_app_version()`, `is_legacy_mode()`, `is_tenant_aware_mode()` in
  `config.py`, and version branches in `auth_deps.py` / `auth_service.py`.
  Worth harvesting the switch idea — but **not** its `_resolve_base_path()`,
  which hardcodes developer machine paths (`D:\Repo6\1001`,
  `D:\project_work\iDEAL Banking 6.0 - Alpha\...`) and walks the disk hunting for
  `Return.xml`. That is unshippable and should be dropped.

`.env` on HEAD already carries `VERSION=5.5` with a comment about tenant-aware
mode — **but nothing in the codebase reads it.** It is dead config today.

---

## 2. Recommended architecture: a Host Profile

Do **not** fork the tree into `src/5.5/` and `src/6.0/`. Two trees means every
NLP fix, every UI fix, every variance-engine fix is done twice — and the diffs
above show ~95% of both trees would be identical.

Instead introduce **one seam**: a `HostProfile` — the single object that knows
"which iDEAL am I hosted by". It answers two kinds of question:

1. *Where is file X for this request?*
2. *Who is this caller, and what returns may they see?*

Resolved **once at startup** from `VERSION`. Every other module stops importing
path constants and starts asking the profile.

### 2.1 Request context

The second seam is a `RequestContext` — a small frozen dataclass
(`login_id`, `tenant_id`) produced by `require_login()` and threaded through the
data layer, replacing today's module-level path globals. In 5.5 mode
`tenant_id` is always `""`; in 6.0 it is required and validated against
`XML_Tenant.xml`. The 6.0 branch already threads `tenant_id: str = ""` through
`service.py` and `report_lookup.py` — that work is directly reusable; we just
pass a `ctx` instead of a bare string so a future host difference doesn't force
another signature sweep.

### 2.2 Proposed folder structure

```
backend/
  config/
    __init__.py            # re-exports settings + get_profile() — the only public surface
    settings.py            # env only, no disk probing: DB, server, CORS, VERSION, AUTH_ENABLED
    context.py             # RequestContext dataclass
  hosts/
    __init__.py            # get_profile() -> HostProfile, memoised on VERSION
    base.py                # HostProfile Protocol/ABC — the documented contract
    ideal_55.py            # single-tenant: Returns.xml / XML_User.xml / XML_Dept.xml, DeptId + Forms
    ideal_60.py            # multi-tenant:  Return.xml  / user.xml     / department.xml, Id + ReturnId
  auth/
    __init__.py
    deps.py                # require_login() -> RequestContext; require_return_access(ctx, return_id)
    service.py             # host-agnostic: caching, 2-hop resolve, role checks — delegates to profile
  data/
    report_lookup.py       # signatures take ctx
    query_xml_lookup.py
    period_lookup.py
    service.py             # orchestration
    calculate_variance.py
    db.py
    xml_loader.py
    models.py
  nlp/                     # unchanged — host-agnostic
  output/                  # FAISS / BM25 indices — unchanged
  main.py                  # routes only
env/
  .env.example
  .env.55.example
  .env.60.example
frontend/src/
  auth/
    index.js               # resolveAuth() -> {loginId, tenantId} + authQuery()
    strategy55.js          # read ?loginId= from location.search
    strategy60.js          # bootstrap _at/_lid -> sessionStorage, decode JWT claims
  api.js                   # calls authQuery() — no auth logic of its own
```

`HostProfile` contract (`backend/hosts/base.py`):

```
returns_xml_path(ctx)             non_xbrl_returns_xml_path(ctx)
table_mapping_base_dir(ctx)       instance_base_dir(ctx)
user_xml_path(ctx)                department_xml_path(ctx)
role_access_xml_path()            period_xml_path(ctx)
query_xml_path(ctx, return_id)
dept_id_attr / forms_attr / nx_forms_attr    # attribute-name mapping
requires_tenant: bool
validate_context(ctx)             # 6.0: tenant exists in XML_Tenant.xml and Status=true
```

Adding iDEAL 7 later = one new file in `hosts/`. No branching anywhere else.

### 2.3 Frontend: one build, runtime strategy

Ship a **single** bundle. Pick the auth strategy at runtime:

- Explicit `VITE_APP_VERSION=5.5|6.0` is authoritative when set.
- Fallback auto-detect: `_at` / `_lid` present -> 6.0; bare `loginId` present -> 5.5.

`api.js` keeps its current shape but replaces every hand-built
`loginId=...&tenantId=...` string with a single `authQuery()` call. Also strip
the ~15 `console.log` statements in the 6.0 `api.js` that print JWT fragments and
login ids to the browser console on every request — a real leak in a production
bank deployment.

---

## 3. Migration phases

> **Status: Phases 0–5 complete** on branch `6-host-profile-config`.
> `VERSION=5.5` / `VERSION=6.0` in the root `.env` is now the only edit
> needed to move the whole project between hosts — backend and frontend.
> Both modes verified end to end against the real repositories; see
> sections 7 and 8. Phases 6–7 (deployment docs, NLP under multi-tenancy)
> remain.

Each phase leaves 5.5 working. 5.5 is in production use, so it is the regression
baseline — re-verify after every phase.

**Phase 0 — baseline. [DONE]** Branch from HEAD (`5-final-55-and-60`). Record current
working 5.5 behaviour for `/auth/my-returns`, `/variance/find`,
`/variance/dates`, `/variance/compute`, `/variance/nlresolve`. These become the
acceptance checks re-run after every phase.

**Phase 1 — config split, no behaviour change. [DONE]** Create `backend/config/`, move
today's `config.py` into `settings.py`, and keep the existing names as
re-exports from `backend/config/__init__.py` so nothing else breaks yet. Wire
`VERSION` -> `APP_VERSION` + `is_legacy_mode()` (harvested from
`3-intigreating-60-55`, minus `_resolve_base_path`). Nothing reads it yet.

**Phase 2 — introduce `RequestContext`. [DONE]** `require_login()` returns a context
instead of a `str`. Update the route handlers in `main.py` and the NLP helpers
that currently take `login_id: str` (`_find_named_return_ids`,
`_build_return_clarification`, `get_relevant_schema`, `rank_within_tables`,
`_shortlist_for_return`, `_shortlist_for_table`). In 5.5 mode `tenant_id=""`
throughout, so behaviour is unchanged.

**Phase 3 — `hosts/` package + `ideal_55.py`. [DONE]** Define the profile contract,
implement the 5.5 profile against today's exact paths and attribute names, and
route `report_lookup`, `query_xml_lookup`, `period_lookup` and `service` through
`get_profile()`. **After this phase 5.5 must behave identically** — test this
phase hardest, it is the one that can break production.

**Phase 4 — `ideal_60.py`. [DONE]** Port the tenant logic from
`2-data-variance-ideal-60`: `XML_Tenant.xml` lookup with the `Status` check,
per-tenant `Database\` roots, the `Id` / `ReturnId` / `NXReturnId` attribute
names. Cache keys become `(tenant_id, login_id)` in both modes (harmless in 5.5).
Fold in the JWT verification fix from section 4.

**Phase 5 — frontend auth strategies. [DONE]** Extract `frontend/src/auth/`, port the
JWT bootstrap from the 6.0 branch, remove the debug logging.

**Phase 6 — env + deployment.** Three `.env` examples; document the IIS reverse
proxy per host (`DV_API_BASE_PATH`, `VITE_API_BASE_URL`, `VITE_BASE_PATH`, and
6.0's `frontend/public/web.config`).

**Phase 7 — NLP under 6.0.** The indices in `backend/output/` were built from one
5.5 schema. Under multi-tenancy, decide whether they are shared or per-tenant,
and whether `nlresolve` filters candidate returns by the tenant's allowed set.
This is the one phase with genuine unknowns — see Q4.

---

## 4. Two things worth flagging before we build

**JWT signatures are never verified.** In the 6.0 flow the React app decodes
`_at` client-side, extracts `LoginId` / `TenantId`, and sends them to FastAPI as
**plain query parameters**, which FastAPI trusts. Anyone can call
`/variance/compute?loginId=admin&tenantId=1001` directly and bypass auth
entirely — the token is decoration. The fix belongs in `hosts/ideal_60.py`:
accept the raw `_at` token (header or param), verify its signature against the
.NET issuer's key, and read the claims **server-side**. Better folded into
Phase 4 than shipped as-is. This affects 5.5 too, which trusts a bare
`?loginId=` — but 5.5 at least runs inside an authenticated MVC session, so the
exposure is narrower.

**`.env` ships live credentials.** `DV_DB_PASSWORD` and the Oracle host are in
the working tree and tracked on some branches. Phase 6 should move to
`env/*.example` with real values injected per environment.

---

## 5. Bugs the disk inspection exposed

These are live defects in the 6.0 branch, all of which the Host Profile removes
by construction. Each was confirmed against the actual repo contents.

**B1 — allowed-forms delimiter is wrong (auth silently broken in 6.0).**
`auth_service._resolve_allowed_forms()` splits on `"|"`. In `D:\Repo6`,
`Department.xml` is comma-delimited: `ReturnId="2029,4089,4070"`. Splitting that
on `|` yields the single token `"2029,4089,4070"`, which matches no return id —
so every user resolves to an effectively empty allow-list and **every**
`/variance/compute` is denied with a 403. This is almost certainly why 6.0 "has
a different authentication" in practice. `forms_delimiter` must be a profile
property (`"|"` for 5.5, `","` for 6.0).

**B2 — `XML_RoleAccess.xml` path is wrong.** The 6.0 config points at
`BASE_PATH\Database\XML_RoleAccess.xml`. `D:\Repo6\DataBase\` contains only
`1001\` and `5001\` (upload staging) — no such file. The real file is
**per-tenant** at `<tenant>\DataBase\RoleAccess.xml`. So
`load_role_access_xml()` returns `None` and `can_generate_instance()` denies
everyone.

**B3 — `OptionId="CreateInstance"` does not exist in 6.0.**
`validate_create_instance_access()` looks for a row with
`OptionId == "CreateInstance"`. 5.5's `XML_RoleAccess.xml` does use named option
ids (`CreateInstance`, `DataVariation`, …), but 6.0's `RoleAccess.xml` uses
**numeric** ids (`1`, `2`, `3`, …). Even with B2 fixed the lookup never matches.
The profile needs an `option_id(name)` mapping, and we need the 6.0 numeric id
for CreateInstance from the .NET team — the one genuinely missing fact.

**B4 — `INSTANCE_BASE_DIR` is not tenant-scoped.** The 6.0 config uses
`BASE_PATH\Instance`. **`D:\Repo6\Instance` does not exist.** Instances live at
`<tenant>\Instance\<returnId>\`. Flagged as a suspicion in the first draft;
now confirmed.

**B5 — `Period.xml` carries no frequency.** `period_lookup.py` reads
`Period_Id` / `Frequency` / `EBRFrequency` / `PeriodName` from 5.5's
`XML_Period.xml`. 6.0's `Period.xml` rows are only `Id` + `PeriodName` — the
reporting frequency moved onto `Return.xml@RepFreq` (`D`/`M`/`Q`/`A`/`x`). So
`calculate_variance.validate_reporting_date()` cannot get a frequency in 6.0 mode
via the current path. The profile needs a `frequency_for_return(ctx, return_id)`
method with two different implementations, not just a different file path.

**B6 — returns master element name differs.** 5.5 is `<Returns>/<Return>`; 6.0 is
`<Document>/<Row>`. Both branches hardcode their own `findall()` tag. Becomes a
profile property (`returns_row_tag`).

**B7 — repo data is inconsistent with the tenant registry.** `XML_Tenant.xml`
lists `1001, 1002, 1003, 1005` all with `Status="true"`, but on disk:

| Tenant | In registry | Folder exists | Has `Return.xml` | Usable |
|---|---|---|---|---|
| 1000 | **no** | yes | no | no |
| 1001 | yes | yes | yes | **yes** |
| 1002 | yes | yes | **no** (only `User.xml`) | no |
| 1003 | yes | **no** | — | no |
| 1005 | yes | **no** | — | no |

**Only tenant 1001 is actually testable.** `validate_context()` must fail fast
with a clear message when a registry-active tenant has no usable repo folder,
rather than surfacing empty result sets. Phase 4 testing should assume 1001 and
treat 1002 as the negative test case.

---

## 6. Remaining open questions

1. **Q1 — 6.0 numeric `OptionId` for CreateInstance** (blocks B3). Needed from
   the .NET team, along with the id for `DataVariation` if this module should
   honour it. Everything else about role access is now known.
2. **Q2 — `RepFreq="x"`.** Several 6.0 returns carry `RepFreq="x"`. Is that
   "not applicable", "ad-hoc", or unset? Determines whether
   `validate_reporting_date()` should skip validation or reject for those returns.
3. **Q3 — `Query.xml` vs `XML_Query.xml`.** Most 6.0 return dirs use
   `XML_Query.xml`, but e.g. `4061\` uses `Query.xml`. Is that a naming
   migration in progress, or two distinct roles? Affects whether
   `query_xml_lookup` needs a candidate list.
4. **Q4 — NLP indices under multi-tenancy.** The `backend/output/` indices were
   built from one 5.5 schema. Tenant 1001's `Return.xml` is a *different* return
   set (QFCRA/banking taxonomies) from 5.5's (RBI). A shared index will surface
   tables that do not exist for the caller. Likely needs per-tenant indices, not
   just post-filtering — heavier than the first draft assumed.
5. **Q5 — JWT issuer key.** What signs `_at`, and how does this service obtain
   the verification key (shared secret, JWKS endpoint, certificate)?
6. **Q6 — deployment shape.** One FastAPI instance per host version, or one
   serving both? This plan assumes **one instance per host**, since `VERSION` is
   a startup-time setting. Serving both from one process means resolving the
   profile per request — doable, but it changes Phase 3.
7. **Q7 — encrypted PII in 6.0 `User.xml`.** `FirstName` / `EmailId` /
   `PhoneNumber` are AES ciphertext. This module only reads `LoginId` (plaintext)
   and `DepartmentId`, so no decryption is needed today — confirm no planned
   feature needs the user's name or email.

---

## 7. Phase 0–3 verification record

Verified on branch `6-host-profile-config` against `D:\Repo5.5`, `VERSION=5.5`.

| Check | Result |
|---|---|
| `import backend.main` | OK |
| `compileall` over config/hosts/auth/data/nlp/main | OK |
| `GET /health` | 200; reports `profile: iDEAL 5.5` and all 10 resolved paths |
| `GET /auth/my-returns?loginId=iris810` | 200; `allowed_count=24`, contract keys preserved |
| `GET /variance/find` (return CIMS_NRD-CSR) | 200; `return_id=2007`, `report_freq=M`, mapping `Database7\Mapping_1.xml`, 3 tables |
| `GET /variance/dates` | 404 with the same message as before (bad table name in the probe) |
| `period_name_for_freq('Q'/'M')` | `Quarterly` / `Monthly` |
| `get_allowed_form_ids('iris810')` | 24 forms; role `101`; `can_generate_instance=True` |
| `is_return_allowed('2001'/'9999')` | `True` / `False` |
| `tables_for_return('10001')` | 2 tables from `XML_Query.xml` |
| `DV_RETURNS_XML_PATH` override | honoured — proves `PATH_OVERRIDES` plumbing |
| `VERSION=6.0` | `HostProfileError` naming Phase 4, as designed |
| `VERSION` normalisation (`5`/`5.5`/`6`/`6.0`/empty) | `5.5`/`5.5`/`6.0`/`6.0`/`5.5` |

### Pre-existing data defect found (not introduced by this refactor)

`D:\Repo5.5\Database\Returns.xml` on this machine is **not well-formed** — two
malformed attributes near the top:

```
line 12:  Id="2044 Name="CIMS_RCA3(Standalone)"      <-- unclosed quote
line 13:  Id=""2064" Name="CIMS_RCA3(Consolidated)"  <-- doubled quote
```

`ElementTree` therefore aborts the whole file and `_parse_returns()` yields **0
returns**, which makes `/variance/find` 404 for every XBRL return.
`NonXBRLReturns.xml` is fine (64 returns), which is why non-XBRL paths still work.

This is a property of the repository data, not the code: the **pre-refactor code
on branch `5-final-55-and-60` returns 0 for the same file** (verified by stashing
these changes and re-running). Repairing those two attributes makes 281 returns
parse and `/variance/find` return 200. Production presumably has a valid copy,
since 5.5 is reported working there — worth confirming, and worth noting that a
single bad attribute anywhere in `Returns.xml` silently disables every XBRL
return rather than just the affected row.

### Notes for whoever picks up Phase 4

- `settings.py` calls `load_dotenv(override=True)`, so `.env` beats OS
  environment variables. Switching host version means editing `.env`, not
  exporting `VERSION` in a shell.
- `_split_ids()` in `backend/auth/service.py` tolerates both `|` and `,`
  regardless of the profile's declared delimiter, so B1 cannot recur from a
  hand-edited master file. The profile's `forms_delimiter` remains the
  documented one.
- `validate_create_instance_access()` returns **`None`** when the host has no
  `OptionId` mapping for the permission (B3/Q1). `can_generate_instance()`
  coerces that to `False` because its caller needs a bool, but the
  indeterminate verdict is logged distinctly so it is not mistaken for a real
  denial. Once the 6.0 numeric id is known, implement `option_id()` in
  `Ideal60Profile`.
- `describe(ANONYMOUS)` is what `/health` reports. `Ideal60Profile` must reject
  `ANONYMOUS` from `validate_context()`; `/health` already handles that and
  reports the paths as tenant-scoped rather than guessing a tenant.

---

## 8. Phase 4-5 verification record

`VERSION` in the root `.env` is the single switch. Verified in **both** modes on
branch `6-host-profile-config`.

### How one value drives the whole project

| Layer | How it learns the version |
|---|---|
| Backend | `settings.APP_VERSION` reads `VERSION`; `hosts.get_profile()` picks the profile |
| Repository root | `DV_BASE_PATH_55` / `DV_BASE_PATH_60`, chosen by the profile itself |
| Frontend | `GET /app-config` at startup, then `src/auth/` picks its strategy |

The frontend needs no rebuild and no second setting. `VITE_APP_VERSION` exists
as an optional build-time pin and is empty by default. If `/app-config` is
unreachable the app falls back to detecting the host from the URL shape
(`_at`/`_lid` means 6.0, bare `loginId` means 5.5), so a proxy misconfiguration
degrades to a working guess instead of an empty screen.

Base path is resolved **per profile**, not once from the module-level `VERSION`.
The first cut got this wrong: `get_profile("6.0")` was handed the 5.5 root, and
because the two layouts are incompatible every lookup failed in a way that read
like a permissions problem. `HostProfile.base_path` now derives from the
profile, so a profile can never be given the other host's tree.

### VERSION=5.5 (unchanged from the Phase 0-3 record)

`/app-config` -> `{version: 5.5, profile: iDEAL 5.5, requires_tenant: false}`;
`/auth/my-returns?loginId=iris810` -> 24 forms; all paths under `D:\Repo5.5`;
a stray `tenantId` is ignored.

### VERSION=6.0, tenant 1001, DV_AUTH_ENABLED=true

| Check | Result |
|---|---|
| `/app-config` | `{version: 6.0, profile: iDEAL 6.0, requires_tenant: true}` |
| Paths | all under `D:\Repo6\1001\DataBase\` + `D:\Repo6\1001\Instance\` |
| `Return.xml` parsed (`<Document>/<Row>`) | 27 returns |
| Allowed forms for `vaibhav@irisindia.net` | **23** — resolved through the comma delimiter (B1 fixed) |
| `is_return_allowed(2029 / 9999)` | `True` / `False` |
| `find_return_and_tables("QCB_F014_Breakdown of Funding by geography")` | 200; `id=4080`, `freq=D`, mapping `4080\TableMapping.xml`, tables `QCB_F014_FILING_INFO`, `QCB_F014_FUNDING_GEO` |
| `Query.xml` fallback (return 4061) | picks `Query.xml`; others pick `XML_Query.xml`; unknown return -> `None` |
| `period_name_for_freq('Q')` | `None` — correct, 6.0 has no frequency column (B5) |
| `can_generate_instance` | `False`, logged as INDETERMINATE (B3 — awaiting Q1) |
| `/health` | reports paths as tenant-scoped rather than inventing a tenant |

Tenant validation, which turns silent empty results into diagnosable errors:

| Tenant | Outcome |
|---|---|
| `1001` | OK |
| `1002` | 403 — active in registry, repository not provisioned (only `User.xml` exists) |
| `1003` | 403 — active in registry, no folder at all |
| `1000` | 403 — folder exists but not in the registry |
| *(none)* | 401 — `tenantId` required |

### Latent search bug fixed (affected 5.5 too)

Two returns were unfindable by their own identity, in **both** hosts:

1. **Exact-name search failed for any name containing a stop word.**
   `extract_keyword()` strips words like "of"/"to"/"for" from the query but not
   from the stored name, so `"Credit to Women(Excel)"` searched as
   `creditwomenexcel` against a field holding `credittowomenexcel`. This was not
   cosmetic: the UI resolves an ambiguous search by re-querying with the chosen
   candidate's **full name**, so those returns could not be opened at all. It hit
   25 names in 5.5 and most 6.0 names, whose titles are prose.

2. **The numeric `Id` was not searchable.** Only `ReturnId` was indexed, which is
   a short code (`R145`), so searching `2007` or `6001` returned nothing.

Both fixed by adding the return's `Id` and the keyword-form of its `Name` to the
searchable fields in `_normalised_returns()`. After the fix, with a valid
`Returns.xml`: `search("2007")` scores an exact match on return 2007, and the
exact-name round-trip succeeds for **280 of 281** 5.5 returns. The one exception
is correct behaviour, not a failure — `test-nbfc` (4071) and `Test_NBFC` (4073)
normalise identically, so a tie and a clarification prompt is the right answer.
The equivalent 6.0 check leaves 2 of 27 asking for clarification, both genuine
substring ambiguities (`F024`, `F027`).

**This changes 5.5 search behaviour** — deliberately, since it makes returns
findable that previously were not. A search that used to score 75 and prompt may
now score 100 and auto-select. Worth a regression pass with real user queries
before this reaches production.

### Local 6.0 repository is internally inconsistent

`D:\Repo6\1001\DataBase\Return.xml` declares returns **4076-4119**, but the
per-return folders on disk are **1001, 2029, 2034, 2065, 2066, 4061-4071, 4080,
5001**. Only **4080** appears in both, which is why it is the only return that
resolves to a table mapping. The masters and the return folders are from
different vintages of the repository. Nothing to fix in code — but 6.0 cannot be
meaningfully load-tested against this copy, and the error message now names both
the `TblPath` tried and the query-file candidates so the gap is obvious rather
than looking like a bug.

Note also that return 2034's `XML_Query.xml` targets an Excel range
(`from [General Information$A5:B5]`) rather than a database table, so extracting
zero tables from it is correct.
