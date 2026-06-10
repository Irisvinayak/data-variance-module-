/**
 * HTTP helpers for the standalone Data Variance application.
 *
 * HOW ROUTING WORKS:
 *   Dev  : Vite proxy in vite.config.js forwards /variance/* and /auth/*
 *          to http://localhost:8002 automatically.
 *          BASE_URL must be '' (empty) so requests go to same origin.
 *
 *   Prod : Set VITE_API_BASE_URL=http://your-backend-server:8002 in .env
 *
 * loginId is appended as ?loginId= on every request so FastAPI's
 * require_login dependency can authorize against XML_User.xml → XML_Dept.xml.
 */

// Leave empty in dev — Vite proxy handles forwarding to FastAPI.
// Set VITE_API_BASE_URL=http://localhost:8002 only if NOT using Vite proxy.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

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