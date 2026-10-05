/**
 * Host-aware identity resolution for the Data Variance app.
 *
 * ONE SWITCH: the backend's VERSION (root .env) decides which iDEAL host this
 * deployment serves. The app asks the backend for it via GET /app-config at
 * startup, so flipping VERSION=5.5 <-> 6.0 needs no frontend rebuild and no
 * second setting to keep in sync.
 *
 * Resolution order, most authoritative first:
 *   1. VITE_APP_VERSION      — explicit build-time pin, for an air-gapped build.
 *   2. GET /app-config        — the backend's own VERSION. The normal path.
 *   3. URL shape auto-detect  — _at/_lid means 6.0, bare loginId means 5.5.
 *
 * Step 3 matters because /app-config can fail (proxy misconfigured, backend
 * still starting) and the app should still authenticate rather than render an
 * empty shell — the URL the host redirected with is itself strong evidence of
 * which host sent us.
 */

import * as strategy55 from './strategy55.js'
import * as strategy60 from './strategy60.js'
import { API_BASE_URL } from '../config.js'
import { getToken, setToken, getClaims } from './token.js'
import { scrubUrlCredentials } from './urlScrub.js'

// Diagnostics are dev-only: production consoles must not carry config hints
// or identity values.
const devLog = {
  warn:  (...a) => { if (import.meta.env.DEV) console.warn(...a) },
  error: (...a) => { if (import.meta.env.DEV) console.error(...a) },
}

const APP_CONFIG_TIMEOUT_MS = 15000

const STRATEGIES = { '5.5': strategy55, '6.0': strategy60 }

let identity = { loginId: '', tenantId: '', uid: '', version: null, ready: false }

const listeners = new Set()

/** Subscribe to identity changes (a host token arriving after startup). */
export function onIdentityChange(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** Whether a live (non-expired) host token is held in memory. */
export function hasToken() {
  return Boolean(getToken())
}

/** The tenant to name in X-Tenant-Id ('' when there is none). The server checks it
 * against the token's own tenant, so it is a claim, never a source of identity. */
export function currentTenantId() {
  return identity.tenantId || ''
}

// ── Host postMessage handshake ───────────────────────────────────────────────
// Inside an iframe we announce DATAVAR_READY and the host may answer with
// DATAVAR_AUTH {jwt}. Hosts that never reply change nothing.
let handshakeStarted = false

function hostOrigins() {
  return String(import.meta.env.VITE_HOST_ORIGIN ?? '')
    .split(',').map((o) => o.trim()).filter(Boolean)
}

function readyTargetOrigin() {
  const configured = hostOrigins()
  if (configured.length) return configured[0]
  try {
    if (document.referrer) return new URL(document.referrer).origin
  } catch { /* fall through */ }
  return '*'   // READY carries no secret
}

export function postReady() {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'DATAVAR_READY' }, readyTargetOrigin())
    }
  } catch { /* non-fatal */ }
}

function handleHostMessage(event) {
  if (event.source !== window.parent) return
  const allowed = hostOrigins()
  if (allowed.length && !allowed.includes(event.origin)) return
  const data = event.data
  if (!data || data.type !== 'DATAVAR_AUTH' || typeof data.jwt !== 'string') return
  if (!setToken(data.jwt)) return   // empty or already expired

  // Claims are unverified display fallbacks only; fill gaps, never override.
  const claims = getClaims()
  const pick = (keys) => {
    for (const k of keys) if (claims?.[k]) return String(claims[k])
    return ''
  }
  const loginId  = identity.loginId  || pick(['LoginId', 'loginId', 'login_id', 'sub'])
  const hostTenant = typeof data.tenantId === 'string' ? data.tenantId.trim() : ''
  const tenantId = identity.tenantId || pick(['TenantId', 'tenantId', 'tenant_id', 'tid']) || hostTenant
  if (identity.ready && (loginId !== identity.loginId || tenantId !== identity.tenantId)) {
    identity = { ...identity, loginId, tenantId }
    listeners.forEach((fn) => fn(identity))
  }
}

/** Idempotent: registers the listener once and sends the first READY. */
export function startHostHandshake() {
  if (handshakeStarted) return
  handshakeStarted = true
  window.addEventListener('message', handleHostMessage)
  postReady()
}

function normalizeVersion(raw) {
  const value = String(raw ?? '').trim()
  if (!value) return null
  if (value.startsWith('5')) return '5.5'
  if (value.startsWith('6')) return '6.0'
  return null
}

async function fetchBackendVersion() {
  // Failure here is NOT benign, so it is never swallowed silently. The app
  // falls back to guessing the version from the URL shape, and a wrong guess
  // against a 6.0 backend is guaranteed-broken rather than merely degraded:
  // strategy55 hardcodes tenantId to '', so every single request 401s with
  // "tenantId required" — an error that points at the URL, several layers away
  // from the actual cause (an unreachable /app-config, e.g. missing from the
  // dev-server proxy list, or a reverse proxy that does not forward it).
  const url = `${API_BASE_URL}/app-config`
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), APP_CONFIG_TIMEOUT_MS)
  try {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, signal: controller.signal })
    if (!res.ok) {
      devLog.error(
        `[auth] GET ${url} -> ${res.status}. Cannot read the backend's VERSION, ` +
        'so the host version will be GUESSED from the URL. If the backend is ' +
        'running 6.0 this guess will be wrong and every request will 401. ' +
        'Check that /app-config is forwarded to the API (vite.config.js proxy, ' +
        'or the reverse-proxy rules in production).',
      )
      return null
    }
    const body = await res.json()
    const version = normalizeVersion(body?.version)
    if (!version) {
      devLog.error('[auth] /app-config returned no usable version:', body)
    }
    return version
  } catch (err) {
    devLog.error(
      `[auth] GET ${url} failed (${err?.message ?? err}). Falling back to ` +
      'guessing the host version from the URL — see above for why that is risky.',
    )
    return null
  } finally {
    clearTimeout(timer)
  }
}

function autoDetectVersion() {
  // 6.0 first: its URL carries _at/_lid, which 5.5 never sends. A 6.0 URL can
  // also carry loginId, so checking 5.5 first would misclassify it.
  if (strategy60.detect()) return '6.0'
  if (strategy55.detect()) return '5.5'
  return null
}

/**
 * Resolve who is using the app. Call once, before rendering.
 * Returns { loginId, tenantId, uid, version, ready }.
 */
export async function bootstrapAuth() {
  // Strip ?_at=<JWT> from the address bar BEFORE any request goes out: the
  // /app-config fetch below would otherwise carry the token in its Referer,
  // and a 5.5 (or wrongly guessed) version never ran the 6.0 strip at all.
  // Idempotent, and it only moves the 6.0 params into sessionStorage.
  strategy60.bootstrap()
  // Session credentials in the URL (the 5.5 session cookie value, a token) are never
  // used and must not stay in the address bar, history or Referer (code review C-01).
  scrubUrlCredentials()
  startHostHandshake()

  const pinned = normalizeVersion(import.meta.env.VITE_APP_VERSION)
  const fromBackend = pinned ? null : await fetchBackendVersion()
  const version = pinned ?? fromBackend ?? autoDetectVersion() ?? '5.5'

  if (!pinned && !fromBackend) {
    // Reached only when the authoritative source was unavailable. Say so
    // plainly — an operator seeing a 401 needs to know the version was a
    // guess, not a fact, because that reframes the whole investigation.
    devLog.warn(
      `[auth] Host version could not be read from the backend; GUESSED "${version}" ` +
      'from the URL shape. If this is wrong, authentication will fail in ways ' +
      'that look like bad credentials rather than a misconfigured proxy.',
    )
  }

  const strategy = STRATEGIES[version] ?? strategy55
  const resolved = strategy.resolve()

  identity = {
    loginId:  resolved.loginId ?? '',
    tenantId: resolved.tenantId ?? '',
    uid:      resolved.uid ?? '',
    version,
    ready:    true,
  }
  return identity
}

/**
 * Auth query string for every API call: 'loginId=..&tenantId=..'.
 *
 * tenantId is always sent, empty under 5.5. The backend ignores it there and
 * requires it under 6.0, so callers never branch on the host version.
 */
export function authQuery() {
  return withAuth().toString()
}

/** Merge the auth params into an existing URLSearchParams-compatible object. */
export function withAuth(params = {}) {
  return new URLSearchParams({
    ...params,
    loginId:  identity.loginId,
    tenantId: identity.tenantId,
  })
}
