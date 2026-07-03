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

export function bootstrapAuthFromUrl() {
  const params = new URLSearchParams(window.location.search)

  // 🔍 DEBUG
  console.log('[api:bootstrap] Full URL:', window.location.href)
  console.log('[api:bootstrap] Search params:', window.location.search)

  const token = params.get('_at')
  const lid   = params.get('_lid')

  console.log('[api:bootstrap] _at from URL:', token ? token.substring(0, 40) + '...' : 'MISSING ❌')
  console.log('[api:bootstrap] _lid from URL:', lid || 'MISSING ❌')

  if (token) {
    sessionStorage.setItem('_at', token)
    console.log('[api:bootstrap] _at written to sessionStorage ✅')
  }
  if (lid) {
    sessionStorage.setItem('_lid', lid)
    console.log('[api:bootstrap] _lid written to sessionStorage ✅')
  }

  // Verify what's actually in sessionStorage after writing
  console.log('[api:bootstrap] sessionStorage._at after write:', sessionStorage.getItem('_at') ? 'EXISTS ✅' : 'EMPTY ❌')
  console.log('[api:bootstrap] sessionStorage._lid after write:', sessionStorage.getItem('_lid') || 'EMPTY ❌')

  if ((token || lid) && window.history.replaceState) {
    params.delete('_at')
    params.delete('_lid')
    const clean = [window.location.pathname, params.toString()].filter(Boolean).join('?')
    window.history.replaceState({}, '', clean)
    console.log('[api:bootstrap] URL cleaned:', clean)
  }
}

function decodeJwtPayload(token) {
  try {
    const base64url = token.split('.')[1]
    if (!base64url) return null
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(base64).split('').map(c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')
    )
    return JSON.parse(json)
  } catch (e) {
    console.error('[api:decodeJwt] Failed to decode JWT:', e)
    return null
  }
}

function getAuthParams() {
  const token   = sessionStorage.getItem('_at') ?? ''
  const payload = decodeJwtPayload(token)

  const loginId  = payload?.LoginId  ?? sessionStorage.getItem('_lid') ?? ''
  const tenantId = payload?.TenantId ?? ''

  // 🔍 DEBUG
  console.log('[api:getAuthParams] called from:', new Error().stack?.split('\n')[2]?.trim())
  console.log('[api:getAuthParams] _at in sessionStorage:', token ? 'EXISTS ✅' : 'EMPTY ❌')
  console.log('[api:getAuthParams] decoded payload:', payload)
  console.log('[api:getAuthParams] loginId:', loginId || 'EMPTY ❌')
  console.log('[api:getAuthParams] tenantId:', tenantId || 'EMPTY ❌')

  if (!loginId)  console.warn('[api:getAuthParams] ❌ loginId empty — auth will fail')
  if (!tenantId) console.warn('[api:getAuthParams] ❌ tenantId empty — auth will fail')

  return (
    `loginId=${encodeURIComponent(loginId)}` +
    `&tenantId=${encodeURIComponent(tenantId)}`
  )
}

export async function getMyReturns() {
  console.log('[api] calling getMyReturns...')
  const res = await fetch(`${BASE_URL}/auth/my-returns?${getAuthParams()}`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Auth error (${res.status})`)
  }
  return res.json()
}

export async function findReturnTables(returnName) {
  console.log('[api] calling findReturnTables, returnName:', returnName)
  const res = await fetch(
    `${BASE_URL}/variance/find?return_name=${encodeURIComponent(returnName)}&${getAuthParams()}`
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `Find error (${res.status})`)
  }
  return res.json()
}

export async function computeVariance(payload) {
  console.log('[api] calling computeVariance, payload:', payload)
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