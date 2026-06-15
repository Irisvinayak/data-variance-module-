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
 * Auth params (loginId + tenantId) are decoded from the JWT stored in
 * localStorage and appended as query params on every request so FastAPI's
 * require_login dependency can authorize against the tenant's XML files.
 */

// Leave empty in dev — Vite proxy handles forwarding to FastAPI.
// Set VITE_API_BASE_URL=http://localhost:8002 only if NOT using Vite proxy.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

// ── JWT decoder ───────────────────────────────────────────────────────────────
// Decodes the payload of a JWT without verifying the signature.
// Verification is the backend's responsibility; we only need the claims here.
function decodeJwtPayload(token) {
  try {
    // JWT structure: header.payload.signature  (all base64url-encoded)
    const base64url = token.split('.')[1]
    if (!base64url) return null

    // base64url → base64 → decode
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
    const json    = decodeURIComponent(
      atob(base64)
        .split('')
        .map(c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0'))
        .join('')
    )
    return JSON.parse(json)
  } catch {
    return null
  }
}

// ── Auth params helper ────────────────────────────────────────────────────────
// Reads the JWT from localStorage, extracts LoginId + TenantId, and returns
// them as a pre-built query string: "loginId=...&tenantId=..."
//
// Change the localStorage key below if your app stores the token differently.
const TOKEN_KEY = '_at'

function getAuthParams() {
  const token   = sessionStorage.getItem(TOKEN_KEY) ?? ''
  const payload = decodeJwtPayload(token)

  const loginId  = payload?.LoginId  ?? ''
  const tenantId = payload?.TenantId ?? ''

  return (
    `loginId=${encodeURIComponent(loginId)}` +
    `&tenantId=${encodeURIComponent(tenantId)}`
  )
}

// ── GET /auth/my-returns?loginId=...&tenantId=... ────────────────────────────
// Fetches the list of return IDs the user is allowed to access.
// Called once on app load — result used to filter all search results.
export async function getMyReturns() {
  const res = await fetch(`${BASE_URL}/auth/my-returns?${getAuthParams()}`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Auth error (${res.status})`)
  }
  return res.json()  // { tenant_id, login_id, allowed_count, allowed_forms: [...] }
}

// ── GET /variance/find?return_name=...&loginId=...&tenantId=... ───────────────
export async function findReturnTables(returnName) {
  const res = await fetch(
    `${BASE_URL}/variance/find?return_name=${encodeURIComponent(returnName)}&${getAuthParams()}`
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Find error (${res.status})`)
  }
  return res.json()
}

// ── POST /variance/compute?loginId=...&tenantId=... ──────────────────────────
export async function computeVariance(payload) {
  const res = await fetch(
    `${BASE_URL}/variance/compute?${getAuthParams()}`,
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