/**
 * HTTP helpers for the standalone Data Variance application.
 *
 * HOW ROUTING WORKS:
 *   Dev  : the Vite proxy in vite.config.js forwards every backend route
 *          prefix to the port VERSION selects in the root .env (5.5 -> 8004,
 *          6.0 -> 8003). BASE_URL must be '' (empty) so requests go to the
 *          same origin and the proxy sees them.
 *
 *   Prod : set VITE_API_BASE_URL (e.g. /DataVar/api) to match the IIS virtual
 *          directory and the web.config rewrite prefix.
 *
 * AUTH: every request carries loginId and tenantId, built by src/auth/. This
 * module does not know or care which iDEAL host supplied them — 5.5 reads a
 * query param, 6.0 decodes a JWT, and both end up in authQuery(). tenantId is
 * sent in both modes (empty under 5.5), so there is no per-host branching here.
 * bootstrapAuth() must have run before any of these functions are called; App
 * awaits it before rendering.
 */

import { authQuery, withAuth, postReady, currentTenantId } from './auth/index.js'
import { getToken, clearToken } from './auth/token.js'
import { API_BASE_URL } from './config.js'

// FastAPI sends `detail` as a string for HTTPException but as a list of
// {loc, msg, ...} objects for request-validation (422) errors; passing the list
// straight to Error() rendered "[object Object]".
function errorDetail(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d) => d?.msg ?? JSON.stringify(d)).join('; ')
  }
  return null
}

const DEFAULT_TIMEOUT_MS = 120_000
const NL_TIMEOUT_MS      = 300_000   // first NL call loads the embedding model lazily

// options: fetch options plus optional { signal, timeoutMs } (consumed here,
// never forwarded to fetch). A caller abort rethrows the native AbortError and
// is NOT reported as a timeout.
async function request(path, label, options = {}) {
  const { signal: callerSignal, timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
  const onCallerAbort = () => controller.abort()
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort()
    else callerSignal.addEventListener('abort', onCallerAbort, { once: true })
  }

  const token = getToken()
  if (token) {
    const headers = new Headers(fetchOptions.headers)
    headers.set('Authorization', `Bearer ${token}`)
    // Name the tenant too. The server never trusts it on its own: it must equal the
    // tenant inside the verified token, or the request is refused (403).
    const tenant = currentTenantId()
    if (tenant) headers.set('X-Tenant-Id', tenant)
    fetchOptions.headers = headers
  }

  try {
    const res = await fetch(`${API_BASE_URL}${path}`, { ...fetchOptions, signal: controller.signal })
    if (res.status === 401 && token) {
      // The host token was rejected: drop it and ask the host for a fresh one.
      clearToken()
      postReady()
    }
    return await parseResponse(res, label)
  } catch (err) {
    if (timedOut && err?.name === 'AbortError') {
      throw new Error('The request timed out. Please try again.')
    }
    throw err
  } finally {
    clearTimeout(timer)
    callerSignal?.removeEventListener('abort', onCallerAbort)
  }
}

/**
 * An HTTP error from the API. `message` is exactly what it always was (the server's `detail`,
 * or the status line), so code that only shows `err.message` is unaffected. The extra fields
 * come from the additive error contract (backend/api/error_contract.py, code review M-24):
 *   status    HTTP status
 *   code      stable machine code: "no_data", "forbidden", "rate_limited", ... (null if the
 *             body was not ours, e.g. an IIS error page)
 *   requestId the server's correlation id, quotable in a support request
 */
export class ApiError extends Error {
  constructor(message, { status = null, code = null, requestId = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

async function parseResponse(res, label) {
  if (!res.ok) {
    // Error bodies are not always JSON (proxy/IIS error pages), and a JSON
    // body can be null — fall back to the status line either way.
    const body = await res.json().catch(() => null)
    throw new ApiError(errorDetail(body?.detail) ?? `${label} error (${res.status})`, {
      status: res.status,
      code: typeof body?.code === 'string' ? body.code : null,
      requestId: body?.request_id ?? res.headers?.get?.('X-Request-ID') ?? null,
    })
  }
  try {
    return await res.json()
  } catch {
    // A 200 with a non-JSON body almost always means the route was not
    // forwarded to the API and the SPA's index.html came back instead.
    throw new Error(`${label} error: the server returned a non-JSON response`)
  }
}

function postJson(body) {
  return {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }
}

// ── GET /auth/my-returns ──────────────────────────────────────────────────────
// Fetches the list of return IDs the user is allowed to access.
// Called once on app load — result used to filter all search results.
export async function getMyReturns() {
  // { login_id, allowed_count, allowed_forms: ["2001","2007",...] }
  return request(`/auth/my-returns?${authQuery()}`, 'Auth')
}

// ── GET /variance/find?return_name=... ────────────────────────────────────────
export async function findReturnTables(returnName) {
  const params = withAuth({ return_name: returnName })
  return request(`/variance/find?${params}`, 'Find')
}

// ── GET /variance/dates?return_id=&table_mapping_path=&table_name= ───────────
// Lists every reporting date that actually has data for this return/table,
// newest first — feeds the manual date dropdown (see ControlBar's DateField)
// so the user picks a real submission date instead of guessing on a calendar.
export async function getAvailableDates(returnId, tableMappingPath, tableName) {
  const params = withAuth({
    return_id:          returnId,
    table_mapping_path: tableMappingPath,
    table_name:         tableName,
  })
  // { dates: ["31-MAR-2025", ...] }
  return request(`/variance/dates?${params}`, 'Dates')
}

// ── POST /variance/compute ───────────────────────────────────────────────────
export async function computeVariance(payload) {
  return request(`/variance/compute?${authQuery()}`, 'Compute', postJson(payload))
}

// ── POST /variance/nlresolve ─────────────────────────────────────────────────
// One-shot: resolves a free-text query (e.g. "total loan") to a known return/
// table/column set via the backend's embedding + LLM layer, resolves any
// date/period intent in the query (or defaults to the latest submission),
// and computes the variance — response is shaped exactly like
// /variance/compute's (table_name, reporting_date, comparison_periods,
// columns, display_columns, rows) plus return_id/return_name/report_freq/
// table_mapping_path/confidence. See LayoutContainer's handleNlpSearch.
//
// When the backend can't confidently resolve the query, it responds 200 OK
// with { needs_clarification: true, dimension, question, options,
// skippable, confidence, resolved_context } instead of a result:
//   - dimension "return" — the query gave no usable table/return signal at
//     all; options list every return the user is authorized for.
//   - dimension "table"  — the return is known but which table/section
//     within it is unclear; options list the tied candidate tables.
// The caller resends the same query with `dimension` (echoed from the
// response), `clarificationAnswer` (the picked option's `id`, or the
// SKIP_ANSWER sentinel to say "just take your best guess"), and
// `resolvedContext` (echoed straight back, unchanged) to get the final
// result. No server-side session is kept between the two calls.
export const SKIP_ANSWER = '__skip__'

// `conversationState` is the previous answer's `conversation_state`, echoed back
// so a follow-up ("only decreases", "details of the first one") refines it.
// Omit it for a brand-new question.
export async function resolveNlQuery(query, { dimension, clarificationAnswer, resolvedContext, conversationState, signal } = {}) {
  return request(`/variance/nlresolve?${authQuery()}`, 'NL resolve', {
    ...postJson({
      query,
      ...(dimension ? { dimension } : {}),
      ...(clarificationAnswer ? { clarification_answer: clarificationAnswer } : {}),
      ...(resolvedContext ? { resolved_context: resolvedContext } : {}),
      ...(conversationState ? { conversation_state: conversationState } : {}),
    }),
    timeoutMs: NL_TIMEOUT_MS,
    ...(signal ? { signal } : {}),
  })
}