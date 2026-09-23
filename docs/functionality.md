# Functionality

Written for developers working on this repository — what the module actually
does, feature by feature, and which code answers for each.

## Domain vocabulary

| Term | Meaning |
|---|---|
| **Return** | A regulatory report definition (e.g. *QCB_F013_Breakdown of exposures by geography*), identified by a numeric `return_id`. Listed in `Returns.xml` (5.5) / `Return.xml` (6.0). |
| **Table** | One Oracle table behind a return. A return usually has several (a filing-info table plus one or more data tables), listed in that return's `TableMapping.xml`. |
| **Reporting date** | The `RDATE` a submission belongs to, `DD-MON-YYYY`. |
| **Reporting frequency** | `D`/`M`/`Q`/… on the return, which decides what "the previous period" means. |
| **Variance** | The change in each numeric cell between the current reporting date and one or more earlier ones. |
| **Non-XBRL return** | A return in the separate `NonXBRLReturns.xml` master, with its own allowed-returns attribute. |

## 1. Find a return

`GET /variance/find?return_name=…`

Searches the returns master by name and answers in one of two ways:

- **Exact/confident match** → the return, its `TableMapping.xml` path, and the
  tables you can compute on.
- **Ambiguous** → scored `candidates` for the user to choose from.

Both XBRL and non-XBRL masters are searched. `has_mapping` tells the UI whether
a candidate actually has a usable table mapping, so it can grey out the ones
that do not.

Code: [backend/api/variance.py](../backend/api/variance.py) →
`data/service.find_return_and_tables` → `backend/data/report_lookup.py`.

Note the results are **not** filtered to the user's allowed returns — search is
open, and authorization is enforced when you try to compute. That is deliberate
(so a user can see a return exists) and is worth knowing before you "fix" it.

## 2. Discover available dates

`GET /variance/dates?return_id=…&table_name=…`

Returns the distinct reporting dates actually present in the table, newest
first, capped server-side. This is what populates the date pickers — the UI
never has to guess which dates hold data.

## 3. Compute variance

`POST /variance/compute`

The core feature. Given a return, a table and a reporting date:

1. Resolve the physical table name — add the Oracle schema and, when
   `IS_SP_TABLE_DATA_ENABLED` is on and the return is not an Excel return,
   the `_DP` suffix. This mirrors the .NET host's own rule.
2. Work out which earlier dates to compare against (below).
3. Fetch both periods' rows.
4. Match rows across periods by a business identifier, not by row order.
5. For each numeric column, compute absolute and percentage change and
   pre-format it for display.

Code: `data/service.compute_variance` → `backend/data/calculate_variance.py`.

### Choosing what to compare against

Two ways, mutually exclusive:

- **`reporting_period: N`** — walk back N periods using the return's own
  reporting frequency.
- **`comparison_dates: [...]`** — explicit dates, newest treated as current.
  When present this **replaces** `reporting_period`. Capped at 3, matching the
  UI's period chips, because the result table renders one column group per
  period.

### Comparison modes

- **`vs_current`** — every prior period is compared against the current one.
  *"How does each of the last 3 quarters differ from now?"*
- **`sequential`** — each period is compared against the one before it.
  *"What changed quarter to quarter?"* `chain_dates` reports the chain used.

### Row matching

Rows are matched on a business identifier built from the mapping's
`PrimaryColumn` (e.g. `COUNTRY|RDATE`), not on position — the two periods can
return rows in different orders, or different row sets entirely. Serial-number
columns (`SRNO`, `SLNO`, `ROWNUM`, …) are excluded from both identification and
comparison, since they change meaning between submissions.

Rows present in one period but not the other are still reported, so an added or
withdrawn row is visible rather than silently dropped.

### Column selection

`selected_columns` restricts what comes back. The response carries both
`columns` (compared) and `display_columns` (shown), which differ when a
restriction is applied.

## 4. Natural-language variance

`POST /variance/nlresolve` — ask a question, get a computed table.

The distinguishing feature is that it **computes**, rather than filling in a
form for you to submit. See [architecture.md](architecture.md#request-flow-the-natural-language-path)
for the pipeline.

### Clarification rounds

When the NLP layer is not confident enough it asks rather than guesses,
returning `needs_clarification` with options along one of two dimensions:

- **`return`** — which report did you mean?
- **`table`** — which table within it?

The user's answer comes back on the same route, with `resolved_context` echoed
verbatim, so there is no server-side session. Every clarification is
`skippable`: `"__skip__"` means *stop asking, use your best guess*, which hands
the still-ambiguous shortlist to the model.

Confidence thresholds (`CONFIDENCE_ASK_FLOOR`, `CONFIDENCE_AUTO_PROCEED`) live
in `backend/nlp/nlp_config.py`.

### Date expressions

`backend/nlp/date_resolver.py` turns "last quarter", "March", "vs the previous two
periods" into a concrete `reporting_date` + `reporting_period`. With no date in
the question it defaults to the **latest date that actually has data**, not
today — so a question asked between submissions still returns something.

### What limits it

Only returns covered by the embedding index can be resolved. A return that
exists but was never indexed will not be found by NL search, and the
clarification list says so rather than silently omitting it.

## 5. Free-form NL database query

`POST /variance/nlquery` — an LLM writes SQL, which is validated and executed.

More open-ended than `nlresolve` and correspondingly more guarded: SELECT-only,
banned-keyword check, and a table/column allowlist built from the caller's own
authorized shortlist.

**Not called by the shipped frontend.** It is a reachable API surface with no UI
behind it; treat that as a product decision that has not been made rather than
as dead code.

## 6. Access control

The chain is identical on both hosts; only the file shapes differ:

```
login_id ──(user master)──► DepartmentId ──(department master)──► allowed return ids
```

Enforced at three layers — see
[architecture.md](architecture.md#where-authorization-is-enforced). Results are
TTL-cached (`AUTH_TTL_SEC`, default 3600).

`GET /auth/my-returns` exposes the resolved allow-list for debugging an access
problem. It is documented in-code as something to restrict or remove in
production.

There is also a role/permission subsystem (`can_generate_instance`,
`RoleAccess.xml`, `option_id`) that is **wired but not yet called** — 6.0 uses
opaque numeric `OptionId` values owned by the .NET application, and the mapping
is unconfirmed. Until it is, `option_id()` returns `None` and callers must treat
that as *cannot determine*, never as *denied*. See
[integration-plan.md](integration-plan.md) B3/Q1.

## 7. Diagnostics

- `GET /health` — profile, version and every resolved repository path (5.5) or
  an explanation of why they are tenant-scoped (6.0). For infra probes.
- `GET /app-config` — the version switch, read by the frontend at startup.
- `GET /variance/nlp-health` — whether the NLP stack can actually work: which
  index folder is in use, whether every index file is present, whether
  faiss/sentence-transformers/rank-bm25 import, and optionally
  (`?check_model=true`) whether the embedding model loads.
- `logs/ai/<date>.jsonl` — one record per LLM call: the question, the generated
  SQL, the validation verdict and the latency. See [logging.md](logging.md).

## What this module deliberately does not do

- **It does not write.** Every database path is SELECT-only, and nothing writes
  to the iDEAL repository.
- **It does not own identity.** The host application authenticates; this module
  only resolves what an already-authenticated login may read.
- **It does not build the embedding index.** That is an external tool; this code
  only reads `artifacts/nlp-index/`.
- **It does not serve both host versions from one process.** `VERSION` is read
  once at import. Run two processes to serve both — see the `DV_ENV_FILE`
  overlay mechanism in [backend/config/settings.py](../backend/config/settings.py).
