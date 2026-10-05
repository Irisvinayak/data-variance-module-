# Data Variance Module — Issues: Solved and Remaining

The single record of the code-review findings for the Data Variance module (iDEAL 5.5 and 6.0). It replaces the earlier review reports and status files.

| | |
|---|---|
| **Updated** | 2026-10-05 |
| **Branch** | `6-host-profile-config` — all changes are uncommitted; nothing is deployed |
| **Findings tracked** | 108 (3 Critical, 24 High, 46 Medium, 35 Low), plus issues found in the host applications |
| **Rule followed** | Do not break working behaviour: every risky change is behind a setting whose default equals the old behaviour, or is additive |

Contents: [1. Summary](#1-summary) · [2. Remaining issues](#2-remaining-issues) · [3. Solved issues](#3-solved-issues) · [4. Still to test](#4-still-to-test) · [5. Suggested order and effort](#5-suggested-order-and-effort) · [6. Reference](#6-reference)

---

## 1. Summary

| Status | 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low | Total |
|---|---|---|---|---|---|
| ✅ Solved and tested | 2 | 9 | 9 | 3 | **23** |
| 🧪 Solved in code, testing incomplete | 0 | 6 | 5 | 3 | **14** |
| 🟡 Partly solved | 1 | 4 | 5 | 2 | **12** |
| ⬜ Not started | 0 | 5 | 27 | 27 | **59** |
| **Total** | 3 | 24 | 46 | 35 | **108** |

- **Critical remaining: 1 (partly solved)** — **C-01**, identity verification. The module side is built and tested; finishing it needs deployment steps and three decisions (§2.1).
- Identifiers use the review's numbering: within each severity they run Security → Correctness → Reliability → Performance → Quality, so the number tells you the group.

### What was verified, and what was not

| Check | Result |
|---|---|
| Data Variance automated tests | **744 pass** (106 at the start of this work) |
| Data Variance frontend tests (vitest + jsdom, added in the 2026-10-05 verification pass) | **82 pass** |
| Lint (`ruff`, rules F, E9, B, PLE) | Clean |
| Frontend builds, 6.0 and 5.5 | Both succeed; each ships one `web.config` (6.0 → port 8003, 5.5 → port 8004) |
| Live HTTP smoke test of the hardened backend | 27 of 27 checks passed |
| 6.0 React host (`datavariation` folder) | 61 pass; production build compiles |
| 6.0 API — the class changed for C-01 | 11 pass (5 new); API compiles |
| 6.0 API — full suite | 168 pass, **13 fail — the same 13 fail on the original code** (not caused by this work) |
| 5.5 website — new view tests | 6 pass; website compiles; `IDEALApies` compiles |
| Bandit | 0 high, 11 medium (all known items below) |
| `pip-audit` | **24 known vulnerabilities in 5 packages** (§2.7) |
| Independent review of the diff | No blockers; 3 real bugs found and fixed |

**Not verified:** Oracle execution, real data retrieval and real variance results (no database was reachable); a real iDEAL 6.0 login token; anything in a real browser; the CI workflow on GitHub; the 5.5 chatbot and login/session flows.

---

## 2. Remaining issues

### 2.1 C-01 — finish identity verification (🔴 Critical, partly solved)

**Already done:** the backend verifies the host's signed token (HS512; signature, expiry, issuer, audience), derives login and tenant from it, and refuses a tenant that does not match (403), a missing/invalid/expired token (401), or a token without a tenant under 6.0 (401). The 6.0 host answers the module's `DATAVAR_READY` with the token and tenant over `postMessage`, to the module's exact origin only. The 5.5 Data Variance pages pass only `?loginId=`. The old signing secret is removed from `web.config`, both `setup-dev-secrets.ps1` scripts and the 5.5 `IDEALApies/appsettings.json`; none of the four projects contains it any more. The 6.0 API and `IDEALApies` now refuse to start without a strong signing key.

**Steps that must happen on the servers**

| # | Step | Owner |
|---|---|---|
| 1 | Generate **one** new secret with a cryptographically secure generator, for example `python -c "import secrets; print(secrets.token_urlsafe(48))"`, or in PowerShell: `$b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)`. Do not use `Get-Random`: it is not secure. Never commit or paste it. | Ops |
| 2 | Set it as a machine-level environment variable `JwtSettings__SecretKey` on the 6.0 API server (and on the `IDEALApies` server if deployed), and as `DV_JWT_SECRET` on the Data Variance server, together with `DV_JWT_ISSUER` and `DV_JWT_AUDIENCE` copied from the API's `JwtSettings`. All must carry the same new value. **The 6.0 API will not start until step 2 is done.** | Ops |
| 3 | Deploy the 6.0 API, the 6.0 React host and the Data Variance module together. Rotating the key logs every user out once. | Ops + teams |
| 4 | Set `DV_IDENTITY_MODE=optional`, watch the logs for a week for rejected tokens, then `required`; then `DV_AUTH_ENABLED=true`. Until `required`, a request without a token falls back to the URL identity. | Ops |
| 5 | Build the Data Variance frontend with `VITE_HOST_ORIGIN=<the 6.0 host origin>`, then turn the CSP header from report-only into enforcing. | Module |
| 6 | Delete `D:\project_work\_c01_backup_2026-10-05` (it holds the old secret) once the new one is live and verified. | Ops |

**Decisions needed**

| # | Decision | Why it blocks |
|---|---|---|
| D1 | How does the 5.5 **chatbot** get `aspSession` without the URL? | `_Layout.cshtml` still puts `aspSession` and `uid` in the chatbot iframe URL. The chatbot backend forwards `aspSession` to the 5.5 API to generate instances, so removing it breaks that feature. It needs a change in the chatbot project as well. |
| D2 | How is 5.5 identity verified? 5.5 has no token, so `?loginId=` can still be edited. Options: (A) the host adds a short-lived signed parameter (HMAC of login + expiry, key from configuration); (B) the module asks the 5.5 site to validate the user's session (a new endpoint there). | Without it, C-01 stays open for 5.5. |
| D3 | Remove the other committed secrets? The 6.0 `web.config` also commits the **database connection string, the NanoBI keys and an AES passphrase**; `IDEALApies` has a second committed key (`SecretKeyAuthSettings`). | Each needs its own server-side provisioning first, because removing it stops the API. |
| D4 | When may each server move to `required` mode? | Needs both hosts live and a week of logs. |

### 2.2 Partly solved (12)

| ID | Sev | Issue | Done | Still to do |
|---|---|---|---|---|
| C-01 | 🔴 | Identity is unauthenticated, and the backend is reachable without IIS | Identity verification built (`DV_IDENTITY_MODE`), tenant bound to the token (403 on mismatch, 401 without), 6.0 host sends the token, 5.5 Data Variance URL carries only `?loginId=`, signing key out of the repositories. | Deployment and three decisions — see §2.1. |
| H-01 | 🟠 | `/auth/my-returns` exposes any user's entitlements | Self-scoped once identity mode is on; the endpoint remains because the UI calls it on load. | Turn on `required` mode; consider restricting the endpoint. |
| H-05 | 🟠 | `/variance/find` is not filtered by entitlement and returns server paths | Server path replaced by the file name; optional `DV_FIND_FILTER_BY_ACCESS` built. | Decide whether to enable it (it changes what users see). |
| H-06 | 🟠 | Prompt injection: the user's question is interpolated raw into prompts | Sanitiser + data notice in both prompts. | Reduced, not eliminated; the SQL validator stays the boundary. |
| H-10 | 🟠 | No security headers on the SPA | `nosniff`, `Referrer-Policy`, CSP in report-only mode. | Needs the real host origin (`VITE_HOST_ORIGIN`) to enforce. |
| M-02 | 🟡 | A stale stored `_at` overrides new `_lid`/`_tid` URL parameters, and an expired… | Token memory-only; any stored token removed at startup. | A legacy `?_at=` URL token is still accepted from old hosts (stripped at once); remove when every host uses postMessage. |
| M-04 | 🟡 | `DV_AUTH_ENABLED=false` disables all authorization with only a one-time… | Startup warning + `DV_STRICT_BIND`. | Not forced, because that would stop the current servers; enable after each server is changed. |
| M-06 | 🟡 | Identifiers are f-string interpolated and no bind variables are used anywhere.… | Table names shape-validated. | Dates and literals are still interpolated; no bind variables (bigger query-builder change). |
| M-20 | 🟡 | Nothing ties `DV_NLP_EMBED_MODEL` to the model that built the index. | Wrong-dimension error names `DV_NLP_EMBED_MODEL`; the recorded model is compared when a build stamp has one. | The shipped build stamps record no model, so that comparison never fires. |
| M-43 | 🟡 | Two error styles (exceptions and `{"error"}` dicts); `/nlresolve` checks… | Shared `NO_DATA_PREFIX` / `is_no_data_error`. | The two error styles (exceptions and `{error}` dicts) remain. |
| L-15 | 🟢 | Many broad `except Exception` blocks (intentional degradation). | Fallback-path tests added for the intent and SQL paths. | Not every `except Exception` fallback is covered. |
| L-24 | 🟢 | 198 findings (143 auto-fixable, mostly style); the only real bug among them is… | Ruff config added; bug rules clean. | Style rules deliberately not enforced. |

**Completion pass, 2026-10-05.** Everything that could be finished without a server, database or browser was finished; what is left for each is below. None of these is "solved and tested" until the real-environment step is done.

| ID | Added in this pass (all additive, default behaviour unchanged) | Left to do (real environment or decision) |
|---|---|---|
| C-01 | `scripts/check_identity_config.py` (`--require`): pre-flight for a server's real environment, never prints the secret; start-up posture log (below) | Steps 1–6 and decisions D1–D4 in §2.1: secret rotation, `optional` → `required`, 5.5 mechanism, other committed secrets |
| H-01 | Start-up warning that `DV_IDENTITY_MODE=off` leaves `/auth/my-returns` answerable for any login; test that a verified token overrides a spoofed `loginId` already existed | Set `required` on each server |
| H-05 | Tests for the single-return, all-candidates-denied, off-by-default and auth-off paths of `DV_FIND_FILTER_BY_ACCESS` | Decision: enable it (users then get "No matching return found" instead of "no access") |
| H-06 | The model's own earlier answer is cleaned before it is echoed in the retry prompt; 12-attack corpus: prompts keep the question on one quoted line, and a model that fully obeys the injection still cannot name a table outside the shortlist or a column outside it | Cannot be eliminated by design; the shortlist + SQL validator stay the boundary |
| H-10 | `frontend/csp.js` + build step: `VITE_HOST_ORIGIN` narrows `frame-ancestors`, `VITE_CSP_ENFORCE=true` switches report-only to enforcing; refuses to enforce without an origin | Build with the real host origin, watch the browser for CSP violation reports, then enforce |
| M-02 | `VITE_ALLOW_URL_TOKEN=false` makes a `?_at=` URL token ignored (it is always stripped from the address bar); default still accepts it | Set it once every host answers the handshake |
| M-04 | `backend/security_posture.py`: one log line per unsafe setting at start-up (auth off, identity off, invalid mode, token mode without a secret); `DV_REQUIRE_AUTH=true` refuses to start with auth off | Turn `DV_REQUIRE_AUTH` on per server after auth is enabled there |
| M-06 | Every identifier spliced into SQL (table, filter, return-code, frequency column) is shape-checked (`is_safe_sql_ref`); dates come only from date objects, values only through `sql_literal` | Bind variables for values need a real Oracle to verify; identifiers can never be bound |
| M-20 | `verify_index_folder(..., embed_dim)` compares each index's vector size with what the configured model really produces; start-up warm-up passes it. The shipped 5.5 and 6.0 indexes verify as 1024-dimensional | Ask the index builder to record the model name in `build_stamp.json` |
| M-43 | One definition of an error result, `result_error()`, used by every route; a test fails if a route reintroduces its own check | The wire format (200 + `{error}` on `/compute`, 500 on `/nlresolve`) is M-24 and needs the frontend |
| L-15 | 22 tests force each remaining fallback (numeric helpers, diagnostics, date scan, BM25, lookup rebuild, row labels, audit log) | A few fallbacks in `api/meta.py` and `db.py` rely on the earlier DB tests |
| L-24 | Extra non-style rules enabled and their findings fixed (E402, SIM115, SIM118, SIM300, PLW1510, PLW2901, E741, C420, W292) | Style rules stay off on purpose (~700 findings, no behaviour change) |

### 2.3 The 59 "not started" issues — worked through on 2026-10-05

Outcome of the pass. **Fixed** = changed and covered by new tests; it can still need a real-environment check, named in the last column. **Partly** = the safe part is done, the rest is named. **Pending** = deliberately not changed, because the fix needs real data, a benchmark, a product decision or a contract change. **Deferred** = a refactor that is not safe to do without a browser or a wider regression net.

| Result | Count | IDs |
|---|---|---|
| ✅ Fixed | 34 | H-12, H-13, H-14, H-16, M-12, M-13, M-15, M-16, M-17, L-05, L-06, L-07, M-18, M-41, L-14, L-18, M-01, M-26, M-27, M-31, M-32, L-22, L-23, L-26, L-30, L-01, L-02, L-04, L-12, L-13, M-25, L-20, L-32, L-34 |
| 🟡 Partly | 9 | M-19, H-07, M-08, L-17, M-07, M-09, M-45, M-42, M-44 |
| ⏸ Pending (data / benchmark / decision / contract) | 8 | M-14, L-09, M-21, M-22, L-10, L-11, M-28, M-24 |
| ⏭ Deferred (refactor) | 8 | M-40, L-28, L-29, M-46, L-25, L-31, L-33, L-35 |

Verification of this pass: **744** backend tests and **82** frontend tests pass, ruff and ESLint are clean, both frontend builds succeed. No Oracle, browser, server or GitHub run was involved.

| ID | Change | Still needs |
|---|---|---|
| H-12 | A change from a zero base is `N/A`, not `0.00%` (colour still follows the direction) | — |
| H-13 | Duplicate keys within one period are listed (`duplicate_identifiers`, `duplicate_notice`) and shown as a banner | — |
| H-14 | Rows are matched on a collision-free tuple key; the display identifier is unchanged | — |
| H-16 | "Previous period" dates are limited to this return's rows and to dates canonical for its frequency | **Oracle**: run a compute on a shared table |
| M-12, M-13 | `parse_all_returns` adds Excel (non-XBRL) returns to the service lookup and the NL lookup | Real 5.5/6.0 repository with an Excel return |
| M-15 | Rows present before but gone now are returned as `removed_rows` + `removed_notice` (main table unchanged) | — |
| M-16 | Explicit NULL policy (`N/A`), `(100)` parsed as −100, NaN/Infinity rejected | — |
| M-17 | Date predicate is a half-open day range, so a DATE with a time part is found | **Oracle** |
| L-05 | Half-up rounding; colour follows the rounded figure | — |
| L-06, L-07 | ISO timestamps parse; fortnightly returns snap to their period end | — |
| M-18 | Column check runs on the parsed SQL: ROUND/CAST/DECODE/ABS and string literals are no longer "hallucinated columns"; real unknown columns and every table/security rule are unchanged | — |
| M-41 | `resolution_mode`: `llm` or `fallback` in the `/nlresolve` response | — |
| L-14 | First JSON object is extracted (`raw_decode`); `selected_columns` must be a list | — |
| L-18 | LLM HTTP session pool sized to the concurrency (`DV_LLM_HTTP_POOL`, default 40) | — |
| M-01 | On a tenant-scoped host a plain per-tenant path override is ignored with a warning; `{tenant}` placeholder supported; `DV_ALLOW_SHARED_PATH_OVERRIDE=true` restores the old behaviour | Check no server relies on a shared override |
| M-26 | `vite preview` proxies the production API base (verified live against a stand-in backend) | — |
| M-27 | Launcher restarts a crashed backend (verified), registration reads ports from `.env`, kills only python, waits up to 120 s | Task Scheduler on the server |
| M-31 | `env_int`/`env_float` name the variable when a numeric setting is mistyped | — |
| M-32 | Default log folder is `logs/<version>/` (`DV_LOG_DIR` still wins) | Tell operators the log path moved |
| L-22, L-23, L-26, L-30 | `.gitignore` anchored; no `8002` or `DOTNET_API_URL` left; `.env.example` has no public IP; proxy-target note corrected | — |
| L-01 | Tenant registry cache is keyed on the file's mtime | — |
| L-02 | `defusedxml` parses the repository XML (entity bombs and external entities refused) | `pip install -r requirements.txt` on servers |
| L-04 | Inbound `X-Request-ID` limited to `[A-Za-z0-9-]` | — |
| L-12 | Orphaned-login cleanup looks only at identity parameters | Browser |
| L-13 | With auth disabled, an unknown tenant is a fixed 403, not a 500 | — |
| M-25 | The dates effect is keyed on the table's ids, so a repeat search no longer wipes the ticked dates; a failed `/dates` shows a retry | Browser |
| L-20 | The chart panel is lazy-loaded: main bundle 602 kB → 222 kB | Browser |
| L-32 | Confidence weights and the lexical bonus are named settings with the old values as defaults | — |
| L-34 | `api/` modules log `[api.variance]` / `[api.nlp]` | — |
| M-19 | `DV_SQL_NUM_PREDICT` raises the token cap (default unchanged) | A real LLM run before raising it |
| H-07 | Start-up warns while the built-in public-IP default is in use; `DV_REQUIRE_EXPLICIT_LLM_URL=true` refuses to start | Set `DV_NLP_OLLAMA_URL` on each server, then drop the code default |
| M-08 | `DV_DOTENV_OVERRIDE=false` (a real OS variable) lets the environment beat `.env`; default unchanged | Audit each server before flipping |
| L-17 | `/ready` (database + LLM booleans, 503 when the database is down, cached 10 s) | Metrics (Prometheus) not added; Oracle for the probe |
| M-07 | Pickles are loaded only when pinned (`scripts/pin_index_hashes.py`, `DV_REQUIRE_INDEX_MANIFEST`) | Operator pins each index; folder permissions |
| M-09 | `DV_LOG_REDACT_IDENTITY=true` logs a stable pseudonym instead of the login/e-mail | Decide to enable; bare 5.5 usernames in free text are not matched |
| M-45 | Accessible names, listbox/option roles, `aria-expanded`, Escape closes the picker | Full combobox keyboard pattern; screen-reader test |
| M-42 | Permissive response models (no output change) for find, compute, dates, my-returns, health, app-config | `/nlresolve` and `/nlquery` not modelled |
| M-44 | ESLint (hook rules) and Vitest added, `npm run lint` in CI | No TypeScript/PropTypes |
| M-14 | Not changed | Needs a real QCB yearly return |
| L-09 | Not changed | Product decision on the comparison-date ceiling |
| M-21, M-22, L-10, L-11 | Not changed: `scripts/eval_retrieval.py` has 6 cases (top-1 4/6, top-k 5/6) — too few to justify changing ranking | A larger benchmark set |
| M-28 | Not changed | Resolve a lock file under the servers' Python version |
| M-24 | Not changed | Contract change; coordinate with the frontend |
| M-40, L-28, L-29, M-46, L-25, L-31, L-33, L-35 | Not changed | Refactors; need browser regression first |

The old per-group tables below are the original findings, kept for the wording of each issue.

### 2.3 Not started (59)

The five open **High** issues are H-07, H-12, H-13, H-14 and H-16: four of them give **wrong numbers with no error**.

#### Wrong numbers and data correctness — needs real data and characterization tests first (14)

| ID | Sev | Issue | What to do |
|---|---|---|---|
| H-12 | 🟠 | A change from 0 is reported as `0.00%` and coloured green | when `prev == 0` and `curr != 0`, return `"N/A"` (or "new"). |
| H-13 | 🟠 | Duplicate business keys silently overwrite each other | flag duplicates in the response (`duplicate_identifiers`), or aggregate them. Never overwrite silently. |
| H-14 | 🟠 | The identifier builder can merge two different rows into one key | use a tuple key, or join all parts on `"\x1f"`. |
| H-16 | 🟠 | The "previous period" lookup ignores the return filter on shared tables | use the same WHERE clause and `validate_reporting_date` filter as `get_available_dates`. |
| M-12 | 🟡 | `_return_meta` searches only the XBRL list, so non-XBRL Excel returns get the `_DP` suffix (ORA-00942 or… | Fall back to `_parse_non_xbrl_returns`, pass `is_non_xbrl=True`. |
| M-13 | 🟡 | The NLP return lookup ignores non-XBRL returns, so their tables are dropped as unauthorized even when granted… | Iterate both lists. |
| M-14 | 🟡 | Needs verification. `A`/`Y`/`H` accept only March/September, and `validate_reporting_date(31-Dec, A/Y/H)` =… | Confirm on real data; derive the months from the profile's `fiscal_year_start_month`. |
| M-15 | 🟡 | Rows present only in a previous period are dropped, with no "−100%" row. | Emit previous-only identifiers with `current=None`. |
| M-16 | 🟡 | `get_difference(None,5)` shows `5` as the change (verified); `(100)` isn't parsed as −100;… | An explicit NULL policy, `(x)`→`-x`, reject non-finite values. |
| M-17 | 🟡 | Needs verification. `fc = TO_DATE(…)` misses DATE values that carry a time part. | `fc >= d AND fc < d+1`. |
| L-05 | 🟢 | Banker's rounding, and colour is decided before rounding (`0.004→0.009` shows `0.00` in green). | `ROUND_HALF_UP`; colour from the rounded value. |
| L-06 | 🟢 | ISO `YYYY-MM-DD HH:MM:SS` strings aren't parsed by `dates_match`. | Add the format. |
| L-07 | 🟢 | No `F`/`FORTNIGHTLY`/`HM` entry, so fortnightly returns skip period-end snapping. | Add the keys. |
| L-09 | 🟢 | `MAX_COMPARISON_DATES=3` counts the current date (2 previous periods in checkbox mode vs 3 via… | Confirm the intent. |

*Blocked by / note:* These change numbers users see. M-14 needs a real QCB yearly return; H-13 and M-17 need a database check.

#### Retrieval and LLM quality — not changed without benchmark proof (9)

| ID | Sev | Issue | What to do |
|---|---|---|---|
| M-18 | 🟡 | False rejections: `ROUND`/`CAST`/`DECODE`/`ABS` are flagged as "hallucinated columns", and labels containing… | Use a parser-based validator (C-03). |
| M-19 | 🟡 | `num_predict: 128` truncates multi-column SQL, and the retry re-sends the full prompt. | Raise the limit; use a compact retry prompt. |
| M-21 | 🟡 | Tie detection uses min-max normalised scores, and a penalised top score ≤ 0 counts as full separation, which… | Relative gap on raw scores; clip before computing the margin. |
| M-22 | 🟡 | `MIN_TABLE_SCORE`/`MIN_COLUMN_SCORE` filter nothing on BGE scores, and row labels have no floor. | Calibrate on `scripts/eval_retrieval.py`. |
| M-41 | 🟡 | The response doesn't say when the deterministic fallback replaced the LLM. | Add `resolution_mode: "llm"\/"fallback"`. |
| L-10 | 🟢 | BM25 takes the global top-15 and then filters, so scoped tables can lose the lexical signal. | Score within the subset. |
| L-11 | 🟢 | Short-token rewrites ("os", "rest", "dom", "acc") can change meaning. | Require context, or remove them. |
| L-14 | 🟢 | The greedy `\{.*\}` extraction breaks on two JSON objects, and `selected_columns` isn't type-checked. | `raw_decode` from the first `{`; validate types. |
| L-18 | 🟢 | A shared `requests.Session` uses the default pool of 10. | Size it to the threadpool. |

*Blocked by / note:* Gate every change with `scripts/eval_retrieval.py` (no drop in top-1/top-3).

#### Configuration, secrets and operations — needs a server step or a decision (13)

| ID | Sev | Issue | What to do |
|---|---|---|---|
| H-07 | 🟠 | The default Ollama URL is a public IP over plain HTTP | no default (fail closed); HTTPS plus a token, or a private network. |
| M-01 | 🟡 | A `DV_*_PATH` override in `.env` is used by every tenant, so tenant 1002 is authorized against tenant 1001's… | Reject per-file overrides when `requires_tenant`, or require a `{tenant}` placeholder. |
| M-08 | 🟡 | `load_dotenv(override=True)`: `.env` beats real OS and orchestrator variables. | `override=False`. |
| M-26 | 🟡 | The new `preview:55/60` scripts don't proxy `/DataVar…/api` (the calls get `index.html`), and… | Add a `preview.proxy` with a rewrite, or drop the scripts. |
| M-27 | 🟡 | Restart-on-failure doesn't cover a crash after launch; the ports are hardcoded and the script force-kills… | Restart loop or NSSM; read the ports from config; kill only `python.exe`; poll for about 120 s. |
| M-28 | 🟡 | Unpinned dependencies (`>=`, caret ranges) with no lock file. | `pip-tools`/`uv` lock; `npm ci`. |
| M-31 | 🟡 | Bare `int()`/`float()` on environment values at import; a typo crashes startup without naming the variable. | `pydantic-settings`. |
| M-32 | 🟡 | Side-by-side 5.5/6.0 processes share `logs/<date>.log` by default (interleaving, lock collisions). | Default to `logs/<version>/` (the new `run_backend.bat` does this). |
| L-17 | 🟢 | No metrics (latency, LLM, DB) and no `/ready` probe that checks the DB and LLM. | Prometheus metrics plus `/ready`. |
| L-22 | 🟢 | `env/` also matches `config/env/`, so `!config/env/*.example` has no effect for new files. | `/env/`. |
| L-23 | 🟢 | Mentions the old port 8002. | Update, or mark that file superseded by this report. |
| L-26 | 🟢 | `DOTNET_API_URL` is read by no code; `.env.example` carries the public Ollama IP. | Remove it; use a placeholder. |
| L-30 | 🟢 | `VITE_PROXY_TARGET` has no effect in builds (only in dev/preview), which misleads operators. | Comment it or remove it. |

*Blocked by / note:* H-07: set `DV_NLP_OLLAMA_URL` on each server first, then drop the code default. M-08 changes `.env` precedence: audit each server's environment first.

#### Security hygiene (7)

| ID | Sev | Issue | What to do |
|---|---|---|---|
| M-07 | 🟡 | `pickle.load` on externally built artifacts allows code execution if the folder is writable. | Lock down the folder ACLs; verify hashes from a manifest; prefer JSON/npz. |
| M-09 | 🟡 | `loginId` (an email under 6.0), full questions and SQL are logged at INFO with 30-day retention and no… | Identity in a header or token; redact query strings; restrict log ACLs. |
| L-01 | 🟢 | The tenant registry is cached for 1 h with no mtime check, so a deactivated tenant is still served. | Key the cache on mtime. |
| L-02 | 🟢 | `xml.etree` on repository XML. | `defusedxml` (cheap hardening). |
| L-04 | 🟢 | The inbound `X-Request-ID` is only length-capped (log forging). | Allow only `[A-Za-z0-9-]`. |
| L-12 | 🟢 | Any query parameter counts as "the URL has identity", which skips the orphaned-login cleanup. | Check the identity keys only. |
| L-13 | 🟢 | With auth bypassed, an unknown tenant gives a 500 instead of a 403. | Call `validate_context` in the bypass branch. |

*Blocked by / note:* M-09 needs a log redaction/retention decision.

#### Frontend (6)

| ID | Sev | Issue | What to do |
|---|---|---|---|
| M-25 | 🟡 | The effect depends on object identity, so it refetches and wipes the user's selected dates; a failed `/dates`… | Key the effect on `return_id`/`table_name`; add an error state. |
| M-40 | 🟡 | God components; every keystroke re-renders the whole toolbar. | Extract hooks or a `useReducer` state machine; split sub-components; memoise the result panels. |
| M-45 | 🟡 | Accessibility: no combobox ARIA, no keyboard handling, icon buttons without `aria-label`, an unlabeled NL… | Follow the WAI-ARIA combobox pattern. |
| L-20 | 🟢 | One 600 kB chunk (recharts loaded eagerly). | Lazy-load `VisualizationPanel` / `manualChunks`. |
| L-28 | 🟢 | About 140 hard-coded colours, inconsistent chart ticks, magic numbers. | CSS custom properties and a constants module. |
| L-29 | 🟢 | Duplicate dropdowns (`DisambigDropdown` / `NlpReturnPicker`) and two near-identical table renderers. | One generic dropdown and one renderer. |

*Blocked by / note:* No blockers.

#### Code quality — refactor only behind tests (10)

| ID | Sev | Issue | What to do |
|---|---|---|---|
| M-24 | 🟡 | `{"error":…}` is returned with 200 on `/compute` but becomes a 500 on `/nlresolve`. | Typed exceptions mapped to 404/422 (coordinate with the frontend). |
| M-42 | 🟡 | No `response_model`, so the OpenAPI response schemas are empty. | Pydantic response models (the shapes are in `docs/models.md`). |
| M-44 | 🟡 | No ESLint (`exhaustive-deps` not enforced), no Vitest/RTL tests, no TypeScript or PropTypes. | Add ESLint and Vitest. |
| M-46 | 🟡 | Mixes the clarification state machine, authorization, retrieval, dates, a retry and response shaping;… | A service-layer pipeline with one authorization gate. |
| L-25 | 🟢 | No `/v1` versioning; `connection_string` is always `None` (dead). | Address at the next contract revision. |
| L-31 | 🟢 | 170–390-line functions; the Ollama client is duplicated in two modules. | Extract steps; one shared LLM client. |
| L-32 | 🟢 | Inline magic weights (2.0/1.0/0.03, 0.45/0.40/0.15, 0.25). | Move them to `nlp_config.py`. |
| L-33 | 🟢 | Very dense history narration that will drift. | Move history to `docs/` and commits; keep the *why*. |
| L-34 | 🟢 | Modules under `backend/api/` still log `[main]`. | Per-module prefixes or `%(name)s`. |
| L-35 | 🟢 | Dead or unused code. | Wire up (6.0 OptionId work) or remove. |

*Blocked by / note:* M-24 changes the `{error}`-with-HTTP-200 contract and must be coordinated with the frontend.


### 2.4 Issues in the host applications (outside this repository)

| ID | Host | Issue | Status |
|---|---|---|---|
| HX-01 | 5.5 | The session cookie value and session id were put in the Data Variance URL | ✅ Fixed in `Ai.cshtml` and `Index.cshtml` (6 new tests). Still present in the chatbot iframe — see HX-05 |
| HX-02 | 6.0 | JWT signing secret committed in `web.config` | 🟡 Removed from all four files; **rotation and server configuration pending** (§2.1) |
| HX-03 | 6.0 | The host sent neither token nor tenant to the module | 🧪 Fixed in `VarianceAI.jsx` (12 tests); not run in a real browser |
| HX-04 | 5.5 | Hardcoded HMAC key and a hardcoded AES passphrase with a fixed IV (`ETLSsoIntegration.cs`, `ClsAESOperations.cs`) | ⬜ Open — host team |
| HX-05 | 5.5 | The chatbot iframe passes `loginId` / `uid` / `aspSession` (`_Layout.cshtml`) | ⬜ Open — decision D1 |
| HX-06 | 5.5 | `XML_Option.xml` in this repository has no `DataVariationAI` row, so the AI page would show "No access" — **verify on the real server** | ❓ Verify |
| HX-07 | 6.0 | `ValidAudience` in the repository is the placeholder `https://localhost:7072` — the deployed value is needed for `DV_JWT_AUDIENCE` | ❓ Verify |
| HX-08 | 5.5 | A token signature is compared with plain `!=`, not a constant-time comparison | ⬜ Open — host team |

### 2.5 Found along the way

- **13 failing 6.0 API tests**, pre-existing: 4 in the authentication controller tests, 3 audit-ignore, 2 data-preparation model, and 1 each in bank-detail, mapping, role-access and data-preparation controller tests. Worth fixing on their own.
- **Other committed secrets** (decision D3): the 6.0 database connection string, NanoBI keys and AES passphrase in `web.config`; `SecretKeyAuthSettings` in `IDEALApies`.
- **A NanoBI single-sign-on token is placed in a URL** by the 6.0 React host (`nanoBiService.js`) — a separate credential-in-URL issue, not touched.
- **ESLint is not installed** in the 6.0 React host, so it could not be linted.
- **None of the three host projects is a git repository**: no history, no easy revert.
- **Someone else added a table-access gate** (`require_tables_access`, used by `/variance/nlresolve` and `/variance/nlquery`, 12 tests). It fails closed and is correct as written.

### 2.6 Dependencies

`pip-audit` found 24 known vulnerabilities in 5 packages installed in the development environment (the servers may differ). Not upgraded: an upgrade changes the production runtime and needs the full suite plus a smoke test as its own step. Also add a lock file (M-28).

| Package | Installed | Fixed in |
|---|---|---|
| `starlette` (the HTTP framework) | 1.2.1 | 1.3.1 — **highest priority** |
| `urllib3` | 2.7.0 | 2.8.0 |
| `anyio` | 4.13.0 | 4.14.2 |
| `cryptography` | 48.0.0 | 49.0.0 / 50.0.0 |
| `pip` | 26.0.1 | 26.1.2 (tooling only) |

---

## 3. Solved issues

### 3.1 Solved and tested (23)

| ID | Sev | Issue | What was done | How it was verified |
|---|---|---|---|---|
| C-02 | 🔴 | The `JOIN` check in `validate_sql` never runs because of a corrupted regex | Backspace byte in the JOIN regex replaced; a test fails the build if a control byte returns. | Tests: joins to other tables rejected; control-byte guard. |
| C-03 | 🔴 | `validate_sql` can be bypassed with a nested sub-query, and there is no… | Parser-based check of every table in the statement (sub-queries, UNION, schema-qualified, table functions, dangerous functions). `/variance/nlquery` off by default. | 48 adversarial SQL cases (independent review) + 35 tests; live: route returns 404. |
| H-02 | 🟠 | The unknown-tenant error lists every active tenant id | One fixed 403 sentence; tenants appear only in the log. | Test. |
| H-03 | 🟠 | Raw exception, ORA and SQL text reach the client | Generic errors with a request id in `errors.py`, `main.py`, `db.py`, `calculate_variance.py`; detail goes to the log. | Tests + live: a database failure returned only a generic message. |
| H-04 | 🟠 | `/health` and `/variance/nlp-health` disclose internals and allow forced model… | Bare `/health`; `/variance/nlp-health` minimal; `check_model` ignored unless `DV_DIAGNOSTICS_ENABLED`. | Tests + live. |
| H-11 | 🟠 | No rate limiting and no input-size limits | Per-login rate limits (240/min, 30/min NL), 256 KB body cap, field limits. | Unit + app tests; live 429 and 413. |
| H-18 | 🟠 | The README setup step wipes `.env` | README setup step corrected (it would have overwritten `.env`). | Read-through; nothing to run. |
| H-19 | 🟠 | A missed `web.config` rename silently routes 6.0 to the 5.5 backend | Vite plugin ships only the matching `web.config`; misleading comments corrected. | Both builds checked: 5.5 → 8004 only, 6.0 → 8003 only. |
| H-20 | 🟠 | `response.json()` is outside the guarded error path | A non-JSON reply from the LLM proxy is handled in both callers. | Mocked-model tests. |
| H-22 | 🟠 | Security boundaries are untested | Tests added for identity, the SQL validator, retrieval authorisation, API and DB layers. | 395 tests pass. |
| H-23 | 🟠 | SQL generation has no circuit breaker | Circuit breaker + concurrency cap for SQL generation. | Mocked tests: trips, fails fast, caps, releases slots. |
| M-03 | 🟡 | `/docs`, `/redoc` and `/openapi.json` are public in every environment. | `/docs`, `/redoc`, `/openapi.json` off unless `DV_ENABLE_API_DOCS`. | Tests + live. |
| M-05 | 🟡 | `return_id` is joined into filesystem paths; with auth off under 5.5, `..\..\X`… | `return_id` shape-validated at the routes and in the service. | Tests + live (400, value not echoed). |
| M-23 | 🟡 | `reporting_period` may be 0 or negative; `comparison_mode` is a free string;… | Request model limits and constrained values (frontend payloads verified). | Tests + live. |
| M-29 | 🟡 | `.faiss` and `.pkl` are loaded independently with no `ntotal == len(meta)` or… | Vector/record count check on load; manifest verifier; shipped indexes pass. | Tests. |
| M-30 | 🟡 | No lock and three dicts updated separately, so a reader can see torn state;… | Report cache: one tuple per key under a lock. | Tests incl. a concurrent read/write test. |
| M-35 | 🟡 | One global lock is held during a tenant's cold lookup build, which blocks every… | One lock per tenant for the NLP lookup build. | Concurrency test: tenant 1002 finishes while 1001 builds. |
| M-37 | 🟡 | Every zero-row result runs `COUNT(*)` plus a DISTINCT over the whole table… | Zero-row full-table diagnostics are opt-in (`DV_TABLE_DIAGNOSTICS`). | Test. |
| M-38 | 🟡 | Unbounded caches; unknown logins are cached for `AUTH_TTL_SEC`, so random ids… | Auth and mapping caches bounded. | Tests. |
| M-39 | 🟡 | The first build holds the global lock while walking every XML file (see M-35… | Same per-tenant build lock as M-35. | Concurrency test. |
| L-08 | 🟢 | `reporting_period` accepts ≤ 0 (silently coerced to 1). | `reporting_period` must be at least 1 (a 0 or negative value is rejected with 422 instead of being silently coerced); the upper bound is unchanged. | Tests (0 and -3 rejected) + live. |
| L-16 | 🟢 | NLS session settings were silently broken until `ff0ba97`. | Tests assert the NLS statements and the two-argument callback signature. | The tests are the fix. |
| L-19 | 🟢 | Per-request linear scans (`columns_for_table`, repeated `parse_returns`). | Precomputed per-table column map. | Tests. |

### 3.2 Solved in code, testing incomplete (14)

**Verification pass, 2026-10-05.** These 14 were tested with stand-ins (fake `fetch`, fake Oracle connections, fake model, jsdom). Result per item; "not verified" means no real database, browser, GitHub run or real 6.0 login was used.

| ID | Result | Evidence | Bug found and fixed |
|---|---|---|---|
| H-08 | ✅ tested | `tests/test_nlquery_route.py` (22): disabled → 404; low/ambiguous confidence asks or 404s without calling the model; SQL hidden unless `DV_NLQUERY_RETURN_SQL`; generic 422/500; the real validator rejects six hostile model replies before Oracle | none |
| H-09 | ✅ tested (client + backend); ⚠️ no real browser | Backend: `test_identity.py`, `test_c01_tenant_and_secret.py` (all modes, expiry, wrong tenant 403). Module `tests/auth.test.js`: memory-only, exact-origin, parent-window-only, expired/malformed rejected, 401 → fresh handshake. 6.0 host: 61 tests incl. origin checks | none |
| H-15 | ✅ tested (mocked); ⚠️ no real Oracle | At cap / below cap / above cap, per-thread flag, HTTP and NL-trimming keep `truncated`; UI test shows the notice | The notice was a toast that vanished after 8 s, so a truncated table could later look complete → persistent banner (`role="alert"`) while the result is on screen |
| H-17 | ✅ tested (jsdom) | `tests/layout.test.jsx`: late response or late error from an abandoned find / candidate selection cannot overwrite the newer result | none |
| H-21 | ✅ tested | `tests/api.test.js`: 120 s / 300 s budgets, abort, caller abort not reported as timeout, timer and listener cleaned up, old timeout cannot affect a newer request | none |
| H-24 | ✅ tested (fakes); ⚠️ no real Oracle | 24 concurrent acquires with cap 3 open exactly 3 sessions; slots released; back-off expires and retries; busy gives the controlled message | A direct connection whose NLS setup failed was never closed (session leak) → closed in `db._acquire` |
| M-10 | ✅ tested (jsdom) | Failed request, timeout → denied with message; non-listed return refused | NL search ignored a failed permissions load → now refused like find. A *successful* empty list still means "backend auth off" and is not filtered by the UI (design; the server enforces) |
| M-11 | ✅ tested | Built bundles: debug logger compiled to a no-op; runtime test shows nothing logged in a production build | Residual: hint text still ships as unused strings; one `console.warn("Failed to load available dates")` is unguarded (server message only, no identity) |
| M-33 | ⚠️ partly verified | In clean copies: `npm ci`, both builds and tests pass; ruff clean; bandit 0 high / 11 medium; `pip-audit -r requirements.txt` found nothing; Python deps resolve on 3.13 | Frontend unit tests were not in the workflow → `npm test` step added. **Never run on GitHub; Python 3.12 not available locally** |
| M-34 | ✅ tested (jsdom) | `tests/app.test.jsx`, `tests/auth.test.js`: error screen, Retry once per click, no loop, `/app-config` cut off at 15 s | none |
| M-36 | ✅ tested; measured | Background, non-blocking, crash-proof, model built once under a race. Measured here: first embedding call 22.7–58.7 s without warm-up vs ~0.1 s after it (warm-up itself took ~22–24 s). Memory not measured | none |
| L-03 | ✅ tested | Configured origin: GET/POST + `Authorization`, `Content-Type`, `X-Tenant-Id` pass; unknown origin, PUT/DELETE/PATCH and other headers get 400 | none (note: `.env` lists `http://localhost/DataVar/` for 5.5 — an origin has no path, so that entry never matches) |
| L-21 | ✅ tested (jsdom) | Disabled while loading, enabled after success / failure / timeout, extra clicks send one request | Enter in the NL box still sent a request per keypress while loading → now blocked |
| L-27 | ✅ tested | No 🎤 or voice handler in the DOM or the production bundle; other controls present | none |


| ID | Sev | Issue | What was done | What is not yet tested |
|---|---|---|---|---|
| H-08 | 🟠 | `/variance/nlquery` has no confidence gate and returns raw errors and SQL | `/nlquery` gets the confidence gate, generic errors, hidden SQL; route is off by default. | Only 'route disabled' is tested; the gate and the hidden SQL are not. |
| H-09 | 🟠 | The 6.0 JWT is in the URL and has no expiry check | Token kept in memory only, `exp` checked, stored token removed; the 6.0 host now delivers it by postMessage (`VarianceAI.jsx`), never in the URL. | Handshake covered by 12 jsdom tests in the host; never run in a real browser across both origins. |
| H-15 | 🟠 | Hitting the row cap gives wrong variance with no warning to the user | Row cap reported: additive `truncated` / `truncation_notice`; notice shown in the UI. | Logic tested with fakes; real Oracle and the UI notice are not. |
| H-17 | 🟠 | Stale responses can overwrite state in find/select | Stale-response guards on find and select-candidate. | Frontend built, not run in a browser; no frontend unit tests. |
| H-21 | 🟠 | No request timeouts in the frontend | Request timeouts + AbortController. | Same: not run in a browser. |
| H-24 | 🟠 | Unbounded unpooled fallback connections with different NLS settings | Timed pool wait, capped fallback with the NLS settings, create-pool backoff. | Fake connections only; the real pool needs a database. |
| M-10 | 🟡 | `isAllowed()` returns true for an empty set, so the dropdown fails open when… | A failed permissions request fails closed. | Not run in a browser. |
| M-11 | 🟡 | The production bundle logs identity and configuration hints to the console, and… | Identity and hints logged in dev builds only. | Not run in a browser. |
| M-33 | 🟡 | No CI pipeline, type checking, `pip-audit` or `bandit`. | `.github/workflows/ci.yml` added. | Has never run on GitHub. |
| M-34 | 🟡 | A rejected or hung `bootstrapAuth` leaves the app on "Starting…" forever; an… | 15 s timeout and a retryable startup error screen. | Not run in a browser. |
| M-36 | 🟡 | No startup warm-up: the first NL request measured 115 s. | Embedding model loads in the background at startup. | Runs and fails safely (tested); the speed-up from ~115 s is not measured. |
| L-03 | 🟢 | CORS allows all methods and headers; only GET/POST are used. | CORS limited to GET/POST/OPTIONS and the headers the UI uses. | Reviewed by reading; no test. |
| L-21 | 🟢 | The search button stays enabled while a request is in flight, so submits pile… | Search button disabled while loading. | Not run in a browser. |
| L-27 | 🟢 | The 🎤 button is a no-op. | Unfinished microphone button hidden. | Not run in a browser. |

### 3.3 What changed, by area

- **Identity:** new `backend/auth/identity.py` (standard library only; pinned algorithms, constant-time signature check, required finite `exp`, issuer/audience checks, every failure a generic 401); `DV_IDENTITY_MODE` = `off` (default) / `optional` / `required`; tenant cross-check against the token (403 on mismatch).
- **SQL safety:** `validate_sql` parses the statement with `sqlglot` and checks every table anywhere in it; fails closed; `/variance/nlquery` off by default; prompt sanitiser keeps the question inside its quotes.
- **Information leaks and limits:** generic errors with a request id; bare `/health`; docs off; server paths hidden; input shape checks; rate limiting and body-size cap; narrowed CORS.
- **Database layer:** row cap reported (`truncated`), timed pool wait, capped fallback with the NLS settings, create-pool backoff, opt-in diagnostics.
- **LLM calls:** circuit breaker and concurrency cap for SQL generation; non-JSON replies handled.
- **Index and caches:** consistency checks, manifest verifier, background warm-up, bounded caches, thread-safe report cache, per-tenant locks.
- **Frontend (module):** token in memory only, timeouts, stale-response guards, fail-closed permissions, startup error screen, credential parameters scrubbed from the address bar, `X-Tenant-Id` sent.
- **6.0 host:** `VarianceAI.jsx` hands the token over by `postMessage`; 6.0 API and `IDEALApies` refuse to start without a strong key.
- **5.5 host:** the two Data Variance pages pass only `?loginId=`.
- **Tests and tooling:** 277 new tests in 8 new files in this repository (plus 12 tests another contributor added), and new tests in each host project; CI workflow; ruff config; README and `.env.example` corrected.

---

## 4. Still to test

| Needs | What to test |
|---|---|
| **A database** | Manual compute for one 5.5 and one 6.0 return (real rows and variances); the four reference NL queries (the first one measures the warm-up gain); a table large enough to hit `DV_DB_MAX_ROWS`, to see the truncation notice; ~30 simultaneous computes (the pool queues, the fallback stays within its cap, a busy pool gives the "busy" message); a wrong table name and a stopped database (the client sees only the generic message, the log has the Oracle detail); the H-12 / H-13 / H-14 / H-16 cases before and after their fixes. |
| **A real 6.0 login** | The full handshake in a browser: host fetches the token, module sends `Authorization` and `X-Tenant-Id`, backend verifies; wrong tenant → 403; expired token → 401 and a fresh handshake; the `optional` → `required` rollout with the real secret, issuer and audience. |
| **A browser** | The same behaviours are now covered by jsdom tests (§3.2); still to confirm in a real browser across both origins: timeouts, stale-response guards, permissions screen, startup error screen, truncation banner. |
| **The servers** | IIS applying the security headers (CSP report-only) and passing `X-Tenant-Id` through the proxy; `JwtSettings__SecretKey` visible to the API process so the startup check passes; `sqlglot` installed; memory use with the startup warm-up. |
| **Gaps in what exists** | The `/nlquery` confidence gate and hidden SQL (H-08); the narrowed CORS rules (L-03); CI on GitHub; the full 5.5 and 6.0 React test suites (only the changed areas were run); the 5.5 chatbot and 5.5 login/session flows. |

---

## 5. Suggested order and effort

1. **Provision and rotate the secret, then roll out `optional` mode** (§2.1). The only way to start closing C-01.
2. **Make decisions D1–D4.**
3. **Fix H-12, H-13, H-14, H-16** with characterization tests and real data — they give wrong numbers with no error.
4. **Upgrade the dependencies** (§2.6) and add a lock file.
5. **Clear the 13 pre-existing 6.0 test failures.**
6. Work through the remaining groups in §2.3.

| Work | Estimate |
|---|---|
| Variance correctness (§2.3, first group), with real-data checks | 1–2 weeks |
| Identity with the host teams (§2.1), mostly coordination and a staged rollout | 1–2 weeks |
| Dependency upgrades and a lock file | 1–2 days plus a regression run |
| Remaining security, operations and frontend items | 1–2 weeks |
| Quality items and refactors | 2–3 weeks |

---

## 6. Reference

### Settings added

All are optional; the defaults reproduce the previous behaviour except where marked.

| Setting | Default | Purpose |
|---|---|---|
| `DV_IDENTITY_MODE` | `off` | `off` / `optional` / `required` JWT verification |
| `DV_JWT_SECRET` | *(empty)* | The host's `JwtSettings:SecretKey` — environment/secret store only, never commit |
| `DV_JWT_ALGORITHMS` | `HS512` | Allowed signing algorithms |
| `DV_JWT_ISSUER`, `DV_JWT_AUDIENCE` | *(empty)* | Checked when set (the host's `ValidIssuer` / `ValidAudience`) |
| `DV_JWT_LEEWAY_SEC` | `300` | Clock skew, matching the host |
| `DV_JWT_LOGIN_CLAIM`, `DV_JWT_TENANT_CLAIM` | `LoginId`, `TenantId` | Claim names |
| `DV_SERVER_HOST` | **`127.0.0.1`** (was `0.0.0.0`) | Bind address |
| `DV_STRICT_BIND` | `false` | `true` = refuse to start if auth is off on a non-loopback host |
| `DV_NLQUERY_ENABLED` | **`false`** | The LLM-written-SQL route |
| `DV_NLQUERY_RETURN_SQL` | `false` | Include generated SQL in `/nlquery` responses |
| `DV_DIAGNOSTICS_ENABLED` | **`false`** | Detailed `/health` and `/variance/nlp-health` |
| `DV_ENABLE_API_DOCS` | **`false`** | `/docs`, `/redoc`, `/openapi.json` |
| `DV_FIND_FILTER_BY_ACCESS` | `false` | Hide returns the caller cannot open from `/variance/find` |
| `DV_RATE_LIMIT_PER_MIN` / `DV_RATE_LIMIT_NL_PER_MIN` | **`240` / `30`** | Per-login budgets; `0` disables |
| `DV_MAX_BODY_BYTES` | **`262144`** | Request body cap |
| `DV_TABLE_DIAGNOSTICS` | `false` | Full-table diagnostics on empty results |
| `DV_NLP_WARMUP` | **`true`** | Load the embedding model at startup |
| `DV_LLM_MAX_CONCURRENT`, `DV_LLM_SLOT_WAIT_SEC` | `4`, `5` | LLM concurrency cap |
| `DV_DB_POOL_WAIT_TIMEOUT_MS`, `DV_DB_FALLBACK_MAX` | **`30000`**, **`5`** | Pool wait and direct-connect cap |
| `DV_AUTH_CACHE_MAX`, `DV_MAPPING_CACHE_MAX` | `5000`, `2000` | Cache bounds |

### Behaviour that changed for users and operators

1. Error messages are generic with a reference id; users no longer see which table or return was missing.
2. `/variance/nlquery` returns 404.
3. `/docs`, the detailed `/health` and `/variance/nlp-health` output are off.
4. Compute requests with `reporting_period` 0 or negative, or an unknown `comparison_mode`, are rejected (422) instead of being silently coerced.
5. Rate limits and the body cap are on.
6. The embedding model loads at startup (memory use appears earlier, not higher).
7. A result cut off at the row cap now carries a visible notice.

---


### Files added or changed

**New — backend:** `auth/identity.py`, `data/validation.py`, `limits.py`, `cache_util.py`, `nlp/prompt_safety.py`, `nlp/llm_guard.py`, `nlp/index_manifest.py`, `nlp/warmup.py`.
**New — frontend:** `src/auth/token.js`.
**New — tests:** `tests/conftest.py`, `test_identity.py`, `test_api_hardening.py`, `test_db_layer.py`, `test_llm_paths.py`, `test_caches_and_service.py`, `test_security_criticals.py`, `nlp/test_index_safety.py`.
**New — project:** `.github/workflows/ci.yml`, `scripts/windows/` (Task Scheduler launcher and registration script from earlier in this work).
**Changed — backend:** `main.py`, `config/settings.py`, `auth/deps.py`, `auth/service.py`, `api/errors.py`, `api/meta.py`, `api/nlp.py`, `api/variance.py`, `data/db.py`, `data/models.py`, `data/service.py`, `data/calculate_variance.py`, `data/report_lookup.py`, `nlp/sql_generator.py`, `nlp/intent_resolver.py`, `nlp/index_store.py`, `nlp/return_lookup.py`, `nlp/schema_info.py`, `nlp/query_analyzer.py`, `dev_server.py`.
**Changed — frontend:** `App.jsx`, `api.js`, `vite.config.js`, `package.json`, `auth/index.js`, `auth/strategy60.js`, `components/ControlBar.jsx`, `components/LayoutContainer.jsx`, `public/web.config`, `public/web.60.config`.
**Changed — project:** `README.md`, `.env.example`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `tests/test_data_layer.py`.

`.env` (git-ignored) was edited locally to `DV_SERVER_HOST=127.0.0.1`. No credential was added to any tracked file.

