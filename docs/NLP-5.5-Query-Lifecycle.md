# iDEAL 5.5 — NLP Section: Full Query Lifecycle

How a typed question such as *"show me the variance in total loan assets for CIMS_RAQ quarterly as of 31-Mar-2025"* becomes a computed variance table.

Everything below is read from the code in `backend/api/nlp.py` and `backend/nlp/*`. Config names are in `backend/nlp/nlp_config.py`; every threshold is overridable by an env var.

---

## 0. Big picture

```
Browser (ControlBar.jsx → api.js)
   │  POST /variance/nlresolve   {query, dimension?, clarification_answer?, resolved_context?, conversation_state?}
   ▼
variance_nlresolve()                      ← conversation layer (follow-ups, refinement questions)
   ▼
_nlresolve_core()                         ← the one-shot pipeline
   1  Analyse query          query_analyzer.py  (+ query_normalizer.py)
   2  Guard: unindexed return?
   3  Validate clarification ids
   4  Retrieval (embeddings)  retriever.py  /  scoped_retriever.py
   5  Confidence gate  ──► may return a CLARIFICATION instead of an answer
   6  Authorisation          require_tables_access / require_return_access
   7  Intent resolution      intent_resolver.py  (QA fast-path → LLM → deterministic fallback)
   8  Map to return + table   service.find_return_and_tables
   9  Date resolution        date_resolver.py  (may ask "which date?")
  10  Compute variance       service.compute_variance (existing, unmodified)
  11  Column targeting + operation analysis   operation_parser.py / analysis.py / series.py
  12  Build response (+ interpretation, provenance)
```

**Design principle:** the LLM never writes SQL on this path. It only *picks* a table and columns from a shortlist that retrieval already built and authorisation already filtered. The SQL is produced afterwards by the existing `calculate_variance.build_query()` through `service.compute_variance()`.

A second route, `POST /variance/nlquery` (free-form NL→SQL), exists but is **disabled by default** (`DV_NLQUERY_ENABLED=false`) and is not used by the shipped UI. See §13.

---

## 1. What is different about 5.5

| Aspect | 5.5 | Source |
|---|---|---|
| Host profile | `Ideal55Profile`, single-tenant, no `tenantId` needed | `backend/hosts/ideal_55.py` |
| Repository root | `DV_BASE_PATH_55` (default `D:\Repo5.5`) | `config/settings.py` |
| Repo files | `Database\Returns.xml`, `XML_User.xml`, `XML_Dept.xml`, `XML_RoleAccess.xml`, `XML_Period.xml`, `<returnId>\XML_Query.xml` | `ideal_55.py` header |
| Embedding index | `artifacts/nlp-index/ideal-55` (override: `DV_NLP_INDEX_DIR_55` or `DV_NLP_INDEX_DIR`) | `nlp_config.index_dir_for()` |
| Returns indexed | CIMS family: ALE Domestic (Q), LR (Fortnightly/Monthly/Quarterly), RAQ (Annual/Monthly/Quarterly), ROR | `ideal-55/build_report.json` |
| How to select | `VERSION=5.5` in `.env`, or an overlay file via `DV_ENV_FILE` | `.env.55.example` |

`VERSION` and the index paths are read **once at import**, so changing version needs a process restart. One process serves one version.

---

## 2. Offline: how the index is built (outside this repo)

The index is produced by an external tool (`D:\Tools\Embeding maker`) and copied into `artifacts/nlp-index/ideal-55/`. This app only reads it.

| File | Contents |
|---|---|
| `table_index.faiss` + `table_meta.pkl` | one vector per table (name, return, column names, descriptions, business concepts) |
| `column_index.faiss` + `column_meta.pkl` | one vector per column: `table.col \| col \| description \| return \| table blurb` |
| `row_label_index.faiss` + `row_label_meta.pkl` | one vector per distinct label value, text `"<table> <column> label: <value>"` — **label values only, not data rows** |
| `concept_index.faiss` + `concept_meta.pkl` | business metrics/concepts |
| `qa_index.faiss` + `qa_meta.pkl` + `qa_pairs.json` | verified question/SQL examples; only the question text is embedded |
| `bm25_table_index.pkl` | BM25 lexical index over tables |
| `schema.json`, `concept_map.json`, `description_samples.json`, `taxonomies.json`, `label_columns_cache.json` | human-readable schema/labels used by `schema_info.py` |
| `build_report.json`, `build_stamp.json` | build provenance; `index_manifest.py` checks these |

Embedding model: **`BAAI/bge-large-en`** (`DV_NLP_EMBED_MODEL`), vectors L2-normalised so inner product = cosine. The index and the runtime must use the same model, or scores are meaningless.

The index metadata carries **no `return_id`**. The runtime always resolves `table → return_id` live from `Returns.xml` + the table-mapping files (`return_lookup.py`), so authorisation never depends on the index content.

---

## 3. Startup

- `warmup.py` loads the ~1.3 GB embedding model in a background thread and checks the index (first uncached request otherwise took ~115 s).
- `index_manifest.py` / `index_store.py` verify the index files and cache each FAISS index in memory, invalidated by file mtime — dropping in a rebuilt folder is picked up without restart.
- NLP modules are imported lazily inside `_nlresolve_core`, so manual routes still work if `faiss-cpu`, `sentence-transformers` or `rank-bm25` is missing. `GET /variance/nlp-health` reports this.

---

## 4. Step-by-step: one request

### Step 0 — The browser sends the question
`ControlBar.jsx` calls `api.js` → `POST /variance/nlresolve` with `NLResolveRequest`:

| Field | Meaning |
|---|---|
| `query` | the typed text |
| `dimension` | set only when answering a clarification: `return`, `table`, `date`, `measure`, `threshold`, `period` |
| `clarification_answer` | the option the user picked (or `__skip__` / latest-date sentinel) |
| `resolved_context` | what was already settled (`return_id`, `table`, `refine`) echoed back so nothing is re-guessed |
| `conversation_state` | the previous answer's state, for follow-ups |

Login is enforced by `Depends(require_login)`, producing a `RequestContext` (`ctx`) with the user's login and department.

### Step 1 — Conversation layer (`variance_nlresolve`)
Runs before the core pipeline. All of it is switchable (`DV_NLP_CONVERSATION`, `DV_NLP_ASK_MEASURE`, `DV_NLP_ASK_THRESHOLD`).

1. **Refinement answers** (`dimension` = measure / threshold / period): the answer is converted to text by `conversation.refinement_text()` and appended to the query via `conversation.effective_query()`; `dimension` is cleared.
2. **Follow-up detection**: if `conversation_state` exists and `conversation.is_followup(text)` (e.g. *"only decreases"*, *"details of the first one"*), `plan_followup()` merges the new text with the earlier turns and **pins the previous return, table and dates** — so the follow-up refines the same result.
3. **Operation parse**: `operation_parser.parse_operation(query)` extracts *what to do with the numbers*: filter, rank (top-N), unchanged, new/removed, trend, anomaly, column and element targets, thresholds.
4. **Refinement question** (`_refinement_question`): asks instead of guessing when
   - a rank is requested but the measure is ambiguous (percentage vs absolute),
   - "significant" is used without a threshold (default `DV_NLP_SIGNIFICANT_PCT`=10),
   - a period such as "Compare March" has no baseline.
   These are returned as `needs_clarification` **before any DB work**.

### Step 2 — Query analysis (`query_analyzer.analyze_query`)
One pass over the raw text, before retrieval, producing a `QueryAnalysis`:

| Output | How |
|---|---|
| `normalized` | `query_normalizer.normalize_query` — fixed dictionary of banking typos/abbreviations (`exposer→exposure`, `npa→non performing asset`, `std→standard`, `dom→domestic`, `ovs→overseas`, …) |
| `return_ids` / `return_names` | return named outright, matched **only against returns that have embeddings** (`indexed_returns.py`) |
| `unindexed_return_names` | a return named that has no embeddings |
| `date_text`, `has_date_intent` | the date/period phrase (tuned for recall; it only locates the span, `date_resolver` decides the meaning) |
| `scope` | domestic / overseas / global |
| `metric_text` | what is left after removing return name, date phrase and boilerplate ("show me the variance in…") — **this is what gets embedded** |

Example: `"show me the variance in total loan assets for CIMS_RAQ quarterly as of 31-Mar-2025"` → `metric_text ≈ "total loan assets"`, return = CIMS_RAQ(Quarterly), date = 31-Mar-2025.

Why: embedding the whole sentence pulls the vector away from the metric, and the return name/date match every table equally. Every extraction falls back to the original text rather than leaving nothing.

### Step 3 — Guard: unindexed return
If the query names a return that has no embeddings (e.g. a return not yet indexed) and no indexed return was named, the API **refuses** with a clarification listing answerable returns. It does not fall through to unscoped retrieval, which previously answered "total number of staff for CIMS_ROR" from CIMS_RAQ at 0.94 confidence.

### Step 4 — Clarification-id validation
Ids coming back from the browser are shape-checked (`ensure_safe_return_id`, `ensure_safe_table_ref`, date regex) before reaching any lookup or path. Invalid → HTTP 400.

### Step 5 — Choosing the retrieval path
`_nlresolve_core` branches on the clarification state:

| Situation | Path |
|---|---|
| `dimension=return` + answer | `_shortlist_for_return(answer)` → scoped retrieval; `__skip__` = best-effort free-form |
| `dimension=table` + answer | `_shortlist_for_table(answer)` — that single table |
| `dimension=date` + answer | re-pin the previous table/return from `resolved_context`; only the date changes |
| **First pass**, query names **>1** return variant | ask "which return?" (e.g. ALE Domestic vs Overseas, or different frequencies — the wrong variant gives wrong comparison periods) |
| First pass, names **exactly 1** return | scoped retrieval inside that return only |
| First pass, names none | free-form `get_relevant_schema` over all indexed returns |

### Step 6 — Retrieval: `retriever.get_relevant_schema` (free-form path)
All embeddings use `embedder.embed_query`: prefix `"Represent this sentence for searching relevant passages: "` + text, `normalize_embeddings=True`.

1. **Embed `metric_text`** → query vector.
2. **Search four signals:**

   | Signal | Index | Top-K searched | Min score | Fusion weight |
   |---|---|---|---|---|
   | Columns (primary) | `column_index` | `TOP_K_COLUMNS×4` = 48 | 0.20 | 2.0 |
   | Row labels (primary) | `row_label_index` | `TOP_K_LABELS×3` = 30 | – | 2.0 |
   | Table description (secondary) | `table_index` | `TOP_K_TABLES×3` = 15 | 0.25 | 1.0 |
   | BM25 (lexical) | `bm25_table_index.pkl` | `BM25_TOP_K` | – | `BM25_SIGNAL_WEIGHT` |

   Columns and row labels are searched **first** and drive ranking: users name a metric or a value, not a table description.
3. **Reciprocal-rank fusion** (`ranking.rrf`, `1/(k+rank+1)`, `k=5`). Each table is scored by its **best** hit per signal, not summed — one precise column match should beat many mediocre ones.
4. **Track signal leaders:** the table holding the #1 hit of each signal. Measured on the 60-query benchmark: a table leading ≥1 signal was right 81% of the time; leading none, 0%.
5. **QA strong-match:** `lexical_search.search_qa_match` compares the whole question with verified QA pairs (embedding prefilter top-20, then string similarity). At ≥ `0.95` it adds a `+10.0` bonus that effectively pins that table.
6. **Lexical overlap bonus:** fraction of query tokens present in the table's texts, × `0.03`.
7. **Metadata demotion:** submission-header tables (`*_FILING_INFO`, gen/sign info) are penalised unless the query asks for filing metadata (`ranking.metadata_demotion`).
8. **De-duplicate** backup copies (`_bckup`, `_bkup`, `_bk` suffix) down to the best-scoring variant.
9. **Resolve `return_id` live** for every candidate via `return_lookup.get_return_for_table` (Returns.xml + table-mapping, with the index text as a hint when a table name appears in several returns). Tables whose `return_id` cannot be resolved are **dropped** — logged as *"dropping N table(s) with unresolved return_id"*.
10. **Named-return scope:** if the analyzer found a return, drop tables from other returns (skipped if that would empty the pool).
11. **Authorisation filter:** `auth.service.get_allowed_form_ids(ctx)` — only tables whose return is allowed to the user's department. (Skipped only when `AUTH_ENABLED=false`.) This runs over the **whole** candidate pool *before* the top-K cut, so a user with few entitlements isn't left with an empty list.
12. **Top-K cut** (`TOP_K_TABLES=5`); columns restricted to surviving tables, de-duplicated, capped at 24; any shortlisted table with no column hits is **back-filled** with its full column list; matched labels capped at 10.
13. **Confidence** (Step 7).

Returns `{tables, columns, matched_labels, table_confidence, table_ambiguous, qa_match}`.

**Scoped path** (`scoped_retriever.rank_within_tables`): same embedding, same fusion arithmetic (`ranking.py`), but restricted with `index_store.subset_search` to the named return's tables. It never widens scope and returns an empty shortlist rather than reaching outside.

### Step 7 — Confidence and the clarify-or-proceed gate (`confidence.table_confidence`)
```
confidence = 0.45 · leader_term      (1 if the winner leads any signal, else 0)
           + 0.40 · margin_term      (min(1, (top1−top2)/top1 ÷ 0.25))
           + 0.15 · lexical_overlap  (of the winner)
```
- QA hit on the winner → confidence `1.0`.
- `table_ambiguous` = two or more candidates within `TIE_EPSILON` (0.08) of the top normalised score.

Thresholds: `CONFIDENCE_AUTO_PROCEED = 0.60`, `CONFIDENCE_ASK_FLOOR = 0.35`.

| Condition | Outcome |
|---|---|
| `confidence < 0.35` or no tables ("weak retrieval") | If the query names exactly one return → use its scoped shortlist instead. Otherwise ask **"which return?"**, with options narrowed to returns that gave any signal |
| Top table has an **identical twin** in another return | ask "which return?" among the twins |
| `table_ambiguous` or `confidence < 0.60` | ask **"which table?"** (`_build_table_clarification`) |
| otherwise | proceed |

Every clarification response carries `resolved_context` and `interpretation`, so the next call can resume exactly where this one stopped.

### Step 8 — Authorisation before any LLM call
`require_tables_access(ctx, shortlist["tables"])` — the tables the model will see have already been authorised.

### Step 9 — Intent resolution (`intent_resolver.resolve_intent`)
Picks one `{return_id, table_name, selected_columns}`, always validated against the shortlist. Order of attempts:

1. **QA fast path (no LLM):** if retrieval pinned a table via a verified QA pair and nothing is ambiguous, the columns are taken from that pair's SQL (`_resolve_from_qa`) — `mode="qa"`.
2. **Cooldown check:** if the LLM endpoint failed recently (`DV_NLP_INTENT_FAILURE_COOLDOWN_SEC`, 60 s), skip straight to deterministic.
3. **LLM call:** prompt = few-shot example + candidate tables with up to `INTENT_MAX_COLS_PER_TABLE` (40) columns each + question + safety notice (`prompt_safety.py`). Model `INTENT_MODEL` (default `qwen2.5:7b`) via Ollama at `DV_NLP_OLLAMA_URL`, timeout `INTENT_TIMEOUT_SEC`.
4. **Grounding check** (`_validate_grounding`): the table must be in the shortlist; the `return_id` must equal that table's resolved id (a `None` id is rejected); selected columns must exist for that table. Anything else → invalid.
5. **One corrective retry** with a shorter prompt if the model answered but badly. A transport failure (unreachable/timeout) is **not** retried — it starts the cooldown.
6. **Deterministic fallback** (`_resolve_deterministic`): top-ranked table and best columns by token scoring against `metric_text`, honouring domestic/overseas suffix rules (`_DOM/_OVE`). `mode="fallback"`.

The result is tagged `resolution_mode` = `qa` | `llm` | `fallback` and recorded by `ai_audit`. An LLM failure never produces a 404 — it degrades to the deterministic answer.

### Step 10 — Map to a concrete return and table
1. `require_return_access(ctx, resolution["return_id"])` — final gate for every branch (clarification branches build shortlists from client-supplied ids).
2. `service.find_return_and_tables(return_name)` → `return_id`, `table_mapping_path`, `report_freq`. If it is ambiguous or has no mapping file → 404. If the re-derived `return_id` differs, it is authorised again.
3. Locate the table in the mapping list. If missing, it is **not an error**: some returns (the CIMS_ALE family) have no usable mapping rows, so the table is carried forward and `XML_Query.xml` provides `filter_col` / `report_freq` (`return_lookup` registers these as an `xml_query` source).
4. `filter_col` (date column, default `RDATE`) and `report_freq` (`M`/`Q`/`A`/…) come from the shortlist metadata.

### Step 11 — Date resolution
1. **Is a date stated?** `analysis.has_date_intent` or `date_resolver.query_mentions_date`.
2. **None stated** and `DV_NLP_ASK_DATE_WHEN_MISSING` is on: `_candidate_dates()` reads the table's real reporting dates; if more than one, return a **"which date?"** clarification (up to `DV_NLP_DATE_OPTIONS`=12). Skipping gives the latest date vs the previous one.
3. **`resolve_reporting_date`** (using `date_intent.parse_date_intent`) maps the phrase to **real dates in the table**, newest first: named dates, month/quarter/half/fiscal-year (fiscal start from the host profile), "last N quarters", "same quarter last year", etc. It returns `(reporting_date, reporting_period, comparison_dates, notes)`.
4. **Strict periods** (`DV_NLP_STRICT_PERIODS`, default on): a period the user named but the table lacks raises *"Data is not available for … in this table"* rather than silently substituting another.
5. **Trend queries** (`operation == "trend"`): `resolve_series_dates` returns the whole window (max `DV_NLP_TREND_MAX_PERIODS`=12).
6. **Follow-ups** with no date of their own reuse the previous turn's dates (`dates_override`).

### Step 12 — Compute (existing code, unchanged)
`service.compute_variance(return_id, table_name, reporting_date, reporting_period, comparison_dates, ctx=…)` — the same call the manual wizard makes.
- It computes **every** numeric column. `selected_columns` is deliberately *not* passed: it also drives the SQL SELECT list, and restricting it dropped `RDATE`/identifier columns and caused a false "no data found".
- **Retry rule:** if the anchor date returns no rows and strict mode does not apply, retry once from the next real date and add a note ("No rows for X; showing Y instead").

### Step 13 — Column targeting and result shaping
- `spec.target_any` ("the trend of Assets") becomes a **column** if one matches, otherwise an **element** (row).
- Named columns beat the LLM's pick; unknown names are reported, and with `DV_NLP_STRICT_NAMES` (default on) a query naming only unknown columns returns 404 instead of answering about other columns.
- "All monetary/numeric columns" → every numeric column.
- `_restrict_result_columns` limits the *result* to the chosen columns.
- **Operation analysis** (fail-safe — a failure here still returns the plain variance):
  - `analysis.analyze_result`: filter by direction/threshold, rank, unchanged, new-from-zero / became-zero / removed rows, anomalies (IQR `1.5`, z `2`).
  - `series.analyze_series` for trends.

### Step 14 — Response
The computed result plus:

| Field | Purpose |
|---|---|
| `return_id`, `return_name`, `report_freq`, `table_mapping_path` (file name only) | what was answered |
| `confidence`, `resolution_mode` | how sure, and by which resolver |
| `interpretation` | what the query was understood to mean: resolved columns **and human labels** (from `schema.json`), reporting date, requested vs used periods, comparison dates, `date_notes` |
| `provenance` | return, table, current/previous dates, rows compared, new/became-zero/removed counts, duplicate keys, truncation, formula text |
| `conversation_state` | passed back by the client on the next turn |
| `drill_down` | for "details of the first one" follow-ups |

---

## 5. Clarification protocol

A response with `needs_clarification: true` has `dimension`, `question`, `options`, `skippable`, `allow_other`, `confidence`, `resolved_context`, `interpretation`. The browser re-posts the **same query** with `dimension` + `clarification_answer` + the echoed `resolved_context`.

| `dimension` | Asked when | Answer resumes at |
|---|---|---|
| `return` | query names several variants; weak retrieval; twin tables; unindexed return | Step 6 scoped to the chosen return |
| `table` | ambiguous or low confidence | Step 9 on that table |
| `date` | no date in the query and several exist | Step 11 with that anchor |
| `measure`, `threshold`, `period` | rank/significance/baseline is underspecified | Step 1, answer appended to the query |

`__skip__` always means "best-effort guess", never an error.

---

## 6. Safety and authorisation checkpoints

1. `require_login` on the route.
2. Ids from the browser shape-validated (Step 4).
3. Retrieval authorisation filter by department over the full candidate pool (Step 6.11).
4. `require_tables_access` before any LLM call (Step 8).
5. Model output grounded against the shortlist; never free-form SQL (Step 9).
6. `require_return_access` on the resolved return, again on the re-derived one (Step 10).
7. Prompt hygiene: `prompt_safety.sanitize_question` / `sanitize_model_echo`, with a data-notice in the prompt.
8. `ai_audit` logs every model call: latency, grounded or not, whether it fell back.
9. Errors are wrapped by `_nlp_stage`, so the failing stage (`query_analysis`, `retrieval`, `scoped_retrieval`, `intent_resolution`, `date_candidates`) is named in the log.

---

## 7. Failure and fallback map

| Failure | Behaviour |
|---|---|
| Ollama unreachable / timeout | cooldown 60 s, deterministic resolver, `resolution_mode="fallback"` |
| LLM answers ungrounded | one retry, then deterministic |
| Named return not indexed | refuse with a list of answerable returns |
| Retrieval finds nothing authorised | return clarification, or 404 *"No accessible return/table matches this query"* |
| Table has no `return_id` | dropped from shortlist (logged) |
| Anchor date has no rows | strict: 404; otherwise retry the next real date with a note |
| Operation analysis throws | plain variance returned, error logged |
| NLP dependency missing | the request fails with the missing package named in the log; manual routes unaffected |

---

## 8. Key settings

| Setting | Default | Effect |
|---|---|---|
| `DV_NLP_EMBED_MODEL` | `BAAI/bge-large-en` | must match the index |
| `DV_NLP_INDEX_DIR_55` / `DV_NLP_INDEX_DIR` | `artifacts/nlp-index/ideal-55` | index location |
| `DV_NLP_TOP_K_TABLES` / `_COLUMNS` / `_LABELS` | 5 / 12 / 10 | shortlist sizes |
| `DV_NLP_MIN_TABLE_SCORE` / `_MIN_COLUMN_SCORE` | 0.25 / 0.20 | cosine floors |
| `DV_NLP_RRF_K` | 5 | rank-fusion constant |
| `DV_NLP_QA_STRONG_MATCH_THRESHOLD` / `_BONUS` | 0.95 / 10.0 | QA pin |
| `DV_NLP_QA_FASTPATH` | true | skip LLM on QA match |
| `DV_NLP_CONFIDENCE_AUTO_PROCEED` / `_ASK_FLOOR` | 0.60 / 0.35 | clarify gate |
| `DV_NLP_TIE_EPSILON` | 0.08 | tie detection |
| `DV_NLP_INTENT_MODEL` | `qwen2.5:7b` | intent LLM |
| `DV_NLP_OLLAMA_URL` | built-in default proxy URL | **set this explicitly for your deployment** |
| `DV_NLP_INTENT_TIMEOUT_SEC` / `_FAILURE_COOLDOWN_SEC` | 20 / 60 | LLM timeouts |
| `DV_NLP_STRICT_PERIODS` / `_STRICT_NAMES` | true / true | refuse rather than substitute |
| `DV_NLP_ASK_DATE_WHEN_MISSING` | on | "which date?" |
| `DV_NLP_CONVERSATION`, `_TREND`, `_ASK_MEASURE`, `_ASK_THRESHOLD` | true | feature switches |
| `DV_NLP_SIGNIFICANT_PCT` / `_DEFAULT_RANK_N` | 10 / 10 | defaults for "significant" and "top" |
| `DV_NLQUERY_ENABLED` | false | free-form NL→SQL route |

---

## 9. Worked example

Query: **"which accounts changed most in total loan assets for CIMS_RAQ quarterly as of 31-Mar-2025"**

1. Analyzer: return = CIMS_RAQ(Quarterly) (id 2041), date = 31-Mar-2025, `metric_text` = "accounts changed most in total loan assets".
2. Operation parser: `rank` by variation, measure ambiguous → **ask**: *percentage or absolute?* User picks → appended to query.
3. One named return → scoped retrieval inside CIMS_RAQ(Quarterly): columns `TOTAL_LOAN_ASSETS` etc. rank first; `CIMS_RAQ_Q_SEC1_PART_A_DOM` leads the column signal; confidence above 0.60 → proceed.
4. Authorisation passes. QA fast path does not apply; the LLM picks the table + `TOTAL_LOAN_ASSETS`, grounding passes (`resolution_mode="llm"`).
5. Date stated → `resolve_reporting_date` → 31-Mar-2025 vs the previous real quarter-end.
6. `compute_variance` runs; the result is restricted to the column, then ranked top-N.
7. Response carries `interpretation` (labelled column, dates), `provenance`, and `conversation_state` so *"only decreases"* can follow.

---

## 10. Source map

| Stage | File |
|---|---|
| Route, orchestration, clarification builders | `backend/api/nlp.py` |
| Conversation / follow-ups | `backend/nlp/conversation.py` |
| Operation parsing | `backend/nlp/operation_parser.py` |
| Query analysis, normalisation | `backend/nlp/query_analyzer.py`, `query_normalizer.py`, `indexed_returns.py` |
| Embedding model | `backend/nlp/embedder.py`, `warmup.py` |
| Index access | `backend/nlp/index_store.py`, `index_manifest.py` |
| Retrieval | `backend/nlp/retriever.py`, `scoped_retriever.py`, `ranking.py`, `lexical_search.py` |
| Return/table resolution | `backend/nlp/return_lookup.py`, `schema_info.py` |
| Confidence | `backend/nlp/confidence.py` |
| Intent (LLM) | `backend/nlp/intent_resolver.py`, `llm_guard.py`, `prompt_safety.py` |
| Dates | `backend/nlp/date_intent.py`, `date_resolver.py` |
| Result analysis | `backend/nlp/analysis.py`, `series.py`, `number_units.py` |
| Host layout | `backend/hosts/ideal_55.py` |
| Config | `backend/nlp/nlp_config.py`, `backend/config/settings.py` |
| Benchmarks | `scripts/eval_retrieval.py`, `scripts/retrieval_bench.py` |

---

## 11. Measured quality (new 5.5 index, 2026-11-22)

`scripts/eval_retrieval.py --from-index 300` (synthetic self-retrieval from column descriptions, no LLM, no Oracle):

- top-1 **68%** (204/300), top-5 **88%** (265/300), about 0.8 s per query.
- All 35 misses are generic column descriptions shared by many tables ("Reporting period end date for the row", "Internal reporting status flag"). Some of those cases name only one acceptable table, so the true figure is probably higher.
- Log warnings worth checking: tables claimed by two returns (`cims_raq_q_sec2_part_c`: 2041 vs 2065), and three monthly RAQ tables dropped for an unresolved `return_id`.

## 12. Known limits

- Row-level data is **not** embedded; only label values (`row_label_index`) and QA pairs are. A row value outside those cannot be found semantically.
- `build_report.json` for 6.0 reported `row_labels: 0` while `row_label_index.faiss` held 184 entries; confirm the 5.5 build the same way before relying on label hits.
- Confidence weights and thresholds were tuned on a 60-query benchmark (`nlp_config.RRF_K` comments); re-measure after an index rebuild.
- The default `DV_NLP_OLLAMA_URL` points at a fixed proxy host; set your own.

## 13. The disabled `/variance/nlquery` route

Free-form NL→SQL: retrieval → same confidence gate → `require_tables_access` → `sql_generator.generate_sql` (model `DV_NLP_OLLAMA_MODEL`, default `sqlcoder-7b-2`) → `validate_sql` (SELECT only, no DML/DDL, tables must be in the shortlist) → `execute_query`. Relative dates anchor on the newest date the top table actually has. Returns only `columns` and `rows` (SQL only if `DV_NLQUERY_RETURN_SQL=true`). Off by default because the regex validator is the only boundary on LLM-authored SQL; enable it only with a read-only DB user limited to the indexed tables.
