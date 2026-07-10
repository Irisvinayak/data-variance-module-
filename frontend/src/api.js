/**
 * HTTP helpers for the standalone Data Variance application.
 *
 * HOW ROUTING WORKS:
 *   Dev  : Vite proxy in vite.config.js forwards /variance/* and /auth/*
 *          to http://localhost:8000 automatically.
 *          BASE_URL must be '' (empty) so requests go to same origin.
 *
 *   Prod : Set VITE_API_BASE_URL=http://your-backend-server:8002 in .env
 *
 * loginId is appended as ?loginId= on every request so FastAPI's
 * require_login dependency can authorize against XML_User.xml → XML_Dept.xml.
 */

// Leave empty in dev — Vite proxy handles forwarding to FastAPI.
// Set VITE_API_BASE_URL=/Datavariance/api for reverse-proxy deployments.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

// ── GET /auth/my-returns?loginId=... ──────────────────────────────────────────
// Fetches the list of return IDs the user is allowed to access.
// Called once on app load — result used to filter all search results.
export async function getMyReturns(loginId = '') {
  const res = await fetch(
    `${BASE_URL}/auth/my-returns?loginId=${encodeURIComponent(loginId)}`
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Auth error (${res.status})`)
  }
  return res.json()  // { login_id, allowed_count, allowed_forms: ["2001","2007",...] }
}

// ── GET /variance/find?return_name=...&loginId=... ────────────────────────────
export async function findReturnTables(returnName, loginId = '') {
  const res = await fetch(
    `${BASE_URL}/variance/find?return_name=${encodeURIComponent(returnName)}&loginId=${encodeURIComponent(loginId)}`
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Find error (${res.status})`)
  }
  return res.json()
}

// ── POST /variance/compute?loginId=... ───────────────────────────────────────
export async function computeVariance(payload, loginId = '') {
  const res = await fetch(
    `${BASE_URL}/variance/compute?loginId=${encodeURIComponent(loginId)}`,
    {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    }
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Compute error (${res.status})`)
  }
  return res.json()
}

// ── POST /variance/nlresolve?loginId=... ─────────────────────────────────────
// One-shot: resolves a free-text query (e.g. "total loan") to a known return/
// table/column set via the backend's embedding + LLM layer, resolves any
// date/period intent in the query (or defaults to the latest submission),
// and computes the variance — response is shaped exactly like
// /variance/compute's (table_name, reporting_date, comparison_periods,
// columns, display_columns, rows) plus return_id/return_name/report_freq/
// table_mapping_path. See LayoutContainer's handleNlpSearch.
export async function resolveNlQuery(query, loginId = '') {
  const res = await fetch(
    `${BASE_URL}/variance/nlresolve?loginId=${encodeURIComponent(loginId)}`,
    {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query }),
    }
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `NL resolve error (${res.status})`)
  }
  return res.json()
}