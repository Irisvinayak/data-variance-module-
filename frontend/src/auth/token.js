/**
 * In-memory holder for the host-issued JWT.
 *
 * The token is NEVER written to sessionStorage/localStorage: it lives only in
 * this module for the life of the page. The payload is decoded ONLY to read
 * `exp` (and display-fallback identity claims). Decoded claims are unverified;
 * the backend is the sole authority on whether a token is valid.
 */

const LEEWAY_SECONDS = 30

let token = null

export function decodeJwtPayload(raw) {
  try {
    const segment = String(raw).split('.')[1]
    if (!segment) return null
    const base64 = segment.replace(/-/g, '+').replace(/_/g, '/').replace(/=+$/, '')
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
    const json = decodeURIComponent(
      atob(padded)
        .split('')
        .map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join(''),
    )
    const claims = JSON.parse(json)
    return claims && typeof claims === 'object' ? claims : null
  } catch {
    return null
  }
}

/** True when the token carries an `exp` that is in the past (30 s leeway). */
export function isExpired(raw, nowMs = Date.now()) {
  const claims = decodeJwtPayload(raw)
  const exp = claims?.exp
  if (typeof exp !== 'number') return false   // no exp claim: let the backend decide
  return exp * 1000 <= nowMs - LEEWAY_SECONDS * 1000
}

export function setToken(raw) {
  const value = typeof raw === 'string' ? raw.trim() : ''
  if (!value || isExpired(value)) {
    token = null
    return false
  }
  token = value
  return true
}

/** Returns the live token, or null (discarding it if it has since expired). */
export function getToken() {
  if (token && isExpired(token)) token = null
  return token
}

export function clearToken() {
  token = null
}

/** Decoded claims of the live (non-expired) token, or null. Unverified. */
export function getClaims() {
  const t = getToken()
  return t ? decodeJwtPayload(t) : null
}
