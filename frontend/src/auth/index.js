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

const STRATEGIES = { '5.5': strategy55, '6.0': strategy60 }

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

let identity = { loginId: '', tenantId: '', uid: '', version: null, ready: false }

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
  const url = `${BASE_URL}/app-config`
  try {
    const res = await fetch(url, { headers: { Accept: 'application/json' } })
    if (!res.ok) {
      console.error(
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
      console.error('[auth] /app-config returned no usable version:', body)
    }
    return version
  } catch (err) {
    console.error(
      `[auth] GET ${url} failed (${err?.message ?? err}). Falling back to ` +
      'guessing the host version from the URL — see above for why that is risky.',
    )
    return null
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
  const pinned = normalizeVersion(import.meta.env.VITE_APP_VERSION)
  const fromBackend = pinned ? null : await fetchBackendVersion()
  const guessed = pinned ?? fromBackend ?? autoDetectVersion() ?? '5.5'
  const version = guessed

  if (!pinned && !fromBackend) {
    // Reached only when the authoritative source was unavailable. Say so
    // plainly — an operator seeing a 401 needs to know the version was a
    // guess, not a fact, because that reframes the whole investigation.
    console.warn(
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

/** The resolved identity. Empty until bootstrapAuth() has run. */
export function getIdentity() {
  return identity
}

/**
 * Auth query string for every API call: 'loginId=..&tenantId=..'.
 *
 * tenantId is always sent, empty under 5.5. The backend ignores it there and
 * requires it under 6.0, so callers never branch on the host version.
 */
export function authQuery() {
  const params = new URLSearchParams({
    loginId:  identity.loginId,
    tenantId: identity.tenantId,
  })
  return params.toString()
}

/** Merge the auth params into an existing URLSearchParams-compatible object. */
export function withAuth(params = {}) {
  return new URLSearchParams({
    ...params,
    loginId:  identity.loginId,
    tenantId: identity.tenantId,
  })
}
