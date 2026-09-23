# Logging

Written for developers working on this repository.

Configured in one place: [backend/logging_config.py](../backend/logging_config.py).

## Streams

| Stream | Destination | Contents |
|---|---|---|
| Console | stdout/stderr | Everything, for local work |
| Daily log | `logs/<YYYY-MM-DD>.log` | Everything, including uvicorn's request lines |
| AI audit | `logs/ai/<YYYY-MM-DD>.jsonl` | One JSON record per LLM call |

Both file streams roll at midnight without a restart and are pruned by
`DV_LOG_RETENTION_DAYS`.

## Line format

```
2026-09-23 10:22:44,081 | INFO | 4fe72836 | backend.auth.service | [AUTH] resolved | login_id='…' tenant_id='1001' | result=4 form(s)
             timestamp   level  request-id   logger name           message
```

### The request id

Every line carries one, so the lines of a single request can be separated from
concurrent ones:

```bash
grep ' 4fe72836 ' logs/2026-09-23.log
```

It is injected by a `logging.Filter` reading a `ContextVar`, so **no call site
passes it** — including third-party libraries and uvicorn. `-` means outside a
request (startup, scripts, tests).

An inbound `X-Request-ID` header is honoured and echoed on the response, so a
trace started by the .NET host or a proxy carries through instead of being
renamed at this boundary. Ask a user reporting a problem for that header value.

Identity (`login_id`, `tenant_id`) rides the same mechanism. It is read from the
query string in the middleware, **not** from `require_login`, because that
dependency is a `def` and runs in a threadpool worker whose context does not
propagate back to the handler.

## Levels

Set with `DV_LOG_LEVEL` (default `INFO`).

- **INFO** — one line per meaningful boundary: a request, an LLM call, an auth
  decision, a cache rebuild.
- **DEBUG** — per-row, per-column, per-candidate-path internals. Also the
  path-probing that records *which* mapping file was chosen, so at INFO you
  cannot tell which XML answered.
- **WARNING / ERROR** — risk-relevant: access denied, no data, invalid input,
  an exception.

`DV_AUTH_ENABLED=false` is announced **once per process** at WARNING, not per
request. It has to stay loud (it must never be true in production) but it is a
property of the deployment, not of any one call.

## Prefixes

Messages carry a `[module]` tag — `[main]`, `[db]`, `[AUTH]`, `[nlp.retriever]`,
`[variance]`. The convention is not perfectly consistent (`backend/data/` uses
bare names and `[table_resolution]` spans two modules; `auth/` shouts). Since
`%(name)s` is already in the format, the useful tags are the sub-stage ones
(`[row_match]`, `[seq_link]`, `[DIAG]`) rather than the module restatements.

## The AI audit trail

`logs/ai/<date>.jsonl` — one JSON object per line, one line per LLM call.

It exists because the prose log could not answer the question an operator
actually asks after a surprising answer: *what did the model produce, and why
was it accepted?* Previously only `prompt_chars` and `response_chars` were
logged; the generated SQL was never recorded on success, the **rejected** SQL
was never recorded at all, and the retry emitted byte-identical text to the
first attempt.

A real record — an attempt that hallucinated a table:

```json
{"ts": "2026-09-22T18:12:32.432+00:00",
 "request_id": "afcc0f4d",
 "login_id": "vaibhav@irisindia.net",
 "tenant_id": "1001",
 "caller": "sql_generator.generate_sql",
 "model": "hf.co/defog/sqlcoder-7b-2:Q5_K_M",
 "query": "total retail exposure by country",
 "prompt_style": "minimal",
 "temperature": 0.0,
 "prompt_chars": 1279,
 "attempt": 1,
 "latency_ms": 61421,
 "sql": "SELECT c.country, SUM(e.total) … JOIN QCB_F013_BREAK_EXPOSURE_CODES c …",
 "valid": false,
 "validation_reason": "Hallucinated tables (not in schema): ['qcb_f013_break_exposure_codes']",
 "failure_category": "hallucinated table"}
```

| Field | Notes |
|---|---|
| `request_id` | Joins to the main log, and to the second attempt of the same call |
| `caller` | `sql_generator.generate_sql` or `intent_resolver.resolve_intent` |
| `attempt` | `1` or `2` — the retry is a separate record |
| `prompt_style`, `temperature` | From the model profile; these change the prompt materially |
| `valid`, `validation_reason`, `failure_category` | Why the SQL was accepted or rejected |
| `ok`, `error` | Present instead when the call failed at transport level |
| `grounded`, `fell_back_to_deterministic` | Intent path only |

Reading it:

```bash
# every rejected generation today
grep '"valid": false' logs/ai/$(date +%F).jsonl

# slowest calls
python -c "import json,sys;[print(d['latency_ms'], d['caller']) for d in map(json.loads, sys.stdin)]" < logs/ai/2026-09-23.jsonl | sort -rn | head
```

Writing an audit record can never fail a request: every error is swallowed
after one warning. An unavailable audit log is a degradation, not an outage.

## What the logs contain — read before enabling DEBUG

These are plaintext files on disk. No credentials are logged (`DB_PASSWORD`
never reaches a log call), but the content is sensitive:

- **`login_id` on nearly every line** — and under iDEAL 6.0 that is an **email
  address**.
- **Full user query text at INFO**, unbounded, in ~30 places.
- **Generated SQL at INFO.** The prompt instructs the model to embed values as
  literals, so bank codes, dates and filter values appear verbatim.
- **Entitlements** — up to 15 return ids a user may access, on a denial.
- **Repository paths including the tenant folder.**

> **`DV_LOG_LEVEL=DEBUG` dumps regulatory submission contents to disk.**
> `calculate_variance` logs per-row, per-column cell values at DEBUG. That is
> useful for diagnosing a variance dispute and inappropriate to leave on. Treat
> DEBUG as a deliberate, time-boxed action, not a default.

The AI audit stream contains the user's question and the generated SQL **by
design** — that is what makes it an audit trail — so it inherits the same
handling.

No redaction or hashing is applied. If your deployment needs it, that is a
decision to take explicitly; it trades away what operators can debug with.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `DV_LOG_LEVEL` | `INFO` | Root level |
| `DV_LOG_DIR` | `<repo>/logs` | Where the daily log goes |
| `DV_AI_LOG_DIR` | `<DV_LOG_DIR>/ai` | Where the audit stream goes |
| `DV_LOG_RETENTION_DAYS` | `30` | Files older than this are deleted on rollover; `0` disables pruning |

`logs/` is gitignored.

## Two things that were broken, so you do not reintroduce them

**The daily handler used to be able to kill file logging silently.** It assigned
the current date *before* opening the new file, so a failed open (disk full,
permission, the file locked by a second process on Windows) left every later
`emit` short-circuiting on the date check and then writing to a `None` stream —
with the `AttributeError` swallowed by `handleError`. It now opens first and
commits state after. **Keep that order.**

**uvicorn's logs used to never reach the file.** uvicorn installs its own
handlers on `uvicorn`/`uvicorn.error`/`uvicorn.access` with `propagate=False`
and never touches the root logger, so the log file had *no HTTP status or
latency line for any request*. `dev_server.py` passes `log_config=None` and
`configure_logging()` re-adopts those loggers defensively for the plain
`uvicorn` CLI case. If you change how the server is launched, check that
`grep -c uvicorn.access logs/<today>.log` is still non-zero.
