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

import { authQuery, withAuth } from './auth/index.js'
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

async function request(path, label, options) {
  const res = await fetch(`${API_BASE_URL}${path}`, options)
  if (!res.ok) {
    // Error bodies are not always JSON (proxy/IIS error pages), and a JSON
    // body can be null — fall back to the status line either way.
    const body = await res.json().catch(() => null)
    throw new Error(errorDetail(body?.detail) ?? `${label} error (${res.status})`)
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

export async function resolveNlQuery(query, { dimension, clarificationAnswer, resolvedContext } = {}) {
  return request(`/variance/nlresolve?${authQuery()}`, 'NL resolve', postJson({
    query,
    ...(dimension ? { dimension } : {}),
    ...(clarificationAnswer ? { clarification_answer: clarificationAnswer } : {}),
    ...(resolvedContext ? { resolved_context: resolvedContext } : {}),
  }))
}