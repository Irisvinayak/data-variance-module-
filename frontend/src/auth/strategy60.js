/**
 * iDEAL 6.0 identity strategy.
 *
 * The .NET Web API host redirects here with a short-lived token in the URL:
 *
 *   /DataVar/?_at=<JWT>&_lid=<loginId>
 *
 * The token is moved into sessionStorage and stripped from the address bar on
 * first load, so a bookmarked or shared URL cannot carry a live token, and a
 * page reload still works. LoginId and TenantId come from the JWT's claims,
 * falling back to _lid / _tid when the token cannot be decoded.
 *
 * TENANT FALLBACK: unlike loginId, tenantId used to have exactly one source —
 * the JWT's TenantId claim — with no fallback at all. That is a real gap, not
 * a theoretical one: it means "the JWT didn't decode, or its TenantId claim
 * uses different casing than expected" and "the .NET host never sent a token"
 * are both indistinguishable from "unauthenticated", and login can appear to
 * succeed (loginId resolves via _lid) while tenantId silently comes back
 * empty and every request 401s with a message that gives no hint why. A
 * plain ?tenantId= / _tid fallback — the same shape loginId already has via
 * _lid — closes that gap for manual testing and for any deployment whose
 * proxy or host does not deliver a working _at.
 *
 * PARAM NAMING: both the short (_lid/_tid, matching _at) and the natural
 * (loginId/tenantId, matching the backend's own query params) spellings are
 * accepted for manual/dev use, whichever is typed. This is deliberately
 * forgiving: someone hand-building a test URL has no way to know this app
 * expects an underscore-prefixed short form rather than the same names the
 * API itself uses, and the failure mode for guessing wrong used to be a bare
 * 401 with no hint which param was missing or misspelled. JWT claims remain
 * authoritative over both when a real token is present.
 *
 * SECURITY: decoding here is for convenience only — it tells the UI who it is
 * talking about. It is NOT authentication. The backend must verify the token's
 * signature itself; see INTEGRATION_PLAN.md section 4. Until it does, these
 * claims (JWT or fallback) are self-asserted and the API is open to anyone who
 * supplies a loginId and tenantId directly.
 */

export const VERSION = '6.0'

const TOKEN_KEY = '_at'
const LOGIN_KEY = '_lid'
const TENANT_KEY = '_tid'

// Natural-name aliases, tried in addition to the short forms above — see the
// PARAM NAMING note at the top of this file.
const LOGIN_KEY_ALIAS = 'loginId'
const TENANT_KEY_ALIAS = 'tenantId'

// The .NET issuer's exact claim names are not pinned down in this codebase, so
// several plausible castings are tried rather than one guessed name — a single
// wrong guess here reproduces this exact "authenticated but tenantId missing"
// failure with no clue in the response as to why.
const LOGIN_CLAIM_KEYS = ['LoginId', 'loginId', 'login_id', 'sub']
const TENANT_CLAIM_KEYS = ['TenantId', 'tenantId', 'tenant_id', 'tid']

function firstClaim(claims, keys) {
  if (!claims) return undefined
  for (const k of keys) {
    if (claims[k] !== undefined && claims[k] !== null && claims[k] !== '') {
      return claims[k]
    }
  }
  return undefined
}

function readStore(key) {
  // sessionStorage throws outright in some embedded/private contexts rather
  // than returning null, and a thrown storage error must not take the app down.
  try {
    return sessionStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeStore(key, value) {
  try {
    sessionStorage.setItem(key, value)
  } catch {
    /* Non-fatal: identity still resolves from the URL for this page load. */
  }
}

export function detect() {
  const params = new URLSearchParams(window.location.search)
  return (
    params.has(TOKEN_KEY) || params.has(LOGIN_KEY) || params.has(TENANT_KEY) ||
    Boolean(readStore(TOKEN_KEY))
  )
  // Deliberately does NOT check the natural-name aliases: a bare ?loginId=
  // with no _at/_lid/_tid is indistinguishable from a 5.5-shaped URL, and
  // this function only runs when /app-config is unreachable and version must
  // be guessed from the URL alone (see auth/index.js). The aliases are read
  // in resolve() below, once the version is already known to be 6.0.
}

/** Move _at/_lid/_tid (and their natural-name aliases) out of the URL and
 * into sessionStorage. Idempotent. */
export function bootstrap() {
  const params = new URLSearchParams(window.location.search)
  const token = params.get(TOKEN_KEY)
  const lid = params.get(LOGIN_KEY) || params.get(LOGIN_KEY_ALIAS)
  const tid = params.get(TENANT_KEY) || params.get(TENANT_KEY_ALIAS)

  if (token) writeStore(TOKEN_KEY, token)
  if (lid) writeStore(LOGIN_KEY, lid)
  if (tid) writeStore(TENANT_KEY, tid)

  // ONLY the token is stripped from the address bar. It is the one real
  // secret here — a live JWT must not survive in a bookmark, a shared link,
  // or a Referer header.
  //
  // The identity params are deliberately LEFT in the URL. Stripping them
  // (as this did originally) creates a half-state that cannot be recovered by
  // reloading: load a URL carrying loginId but no tenantId, and this would
  // store the login, erase the params, and every subsequent reload would then
  // resolve a stale login with a permanently empty tenant — looking
  // authenticated while 401ing on every request, with the URL that could have
  // fixed it already gone. loginId/tenantId are identifiers rather than
  // credentials (5.5 leaves loginId in the URL for the life of the session),
  // so keeping them costs nothing and makes reload idempotent and the current
  // identity visible when something goes wrong.
  if (token && window.history.replaceState) {
    params.delete(TOKEN_KEY)
    const query = params.toString()
    window.history.replaceState(
      {}, '', query ? `${window.location.pathname}?${query}` : window.location.pathname,
    )
  }
}

function decodeJwtPayload(token) {
  try {
    const segment = token.split('.')[1]
    if (!segment) return null
    const base64 = segment.replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
    const json = decodeURIComponent(
      atob(padded)
        .split('')
        .map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join(''),
    )
    return JSON.parse(json)
  } catch {
    // A malformed token is not fatal — _lid/_tid still identify the caller,
    // and the backend is the authority on whether the request is allowed.
    return null
  }
}

export function resolve() {
  bootstrap()
  const token = readStore(TOKEN_KEY)
  const claims = token ? decodeJwtPayload(token) : null

  const loginId = firstClaim(claims, LOGIN_CLAIM_KEYS) ?? readStore(LOGIN_KEY)
  const tenantId = firstClaim(claims, TENANT_CLAIM_KEYS) ?? readStore(TENANT_KEY)

  if (token && claims && !firstClaim(claims, TENANT_CLAIM_KEYS)) {
    // The token decoded fine but none of the known claim names were present —
    // worth a console note, since this is exactly the class of failure that
    // otherwise surfaces only as a 401 with no indication of the cause.
    console.warn(
      '[auth:6.0] JWT decoded but no TenantId claim found under any of',
      TENANT_CLAIM_KEYS, '— falling back to _tid/tenantId param.',
    )
  }

  if (loginId && !tenantId) {
    // The single most common failure in this codebase's testing so far:
    // loginId resolves (via a claim or _lid/loginId) but tenantId does not,
    // and the resulting 401 ("provide a valid tenantId") gives no clue that
    // the fix is simply a missing/misspelled query param. Surface it here,
    // at the point identity actually gets resolved, rather than leaving it
    // to be found by reading the backend's access log.
    console.warn(
      '[auth:6.0] loginId resolved to', JSON.stringify(loginId),
      'but tenantId is empty — every request will 401. iDEAL 6.0 requires a ' +
      'tenant. Add &tenantId=<id> or &_tid=<id> to the URL ' +
      '(e.g. ?loginId=' + loginId + '&tenantId=1001), or confirm the .NET ' +
      "host's JWT actually carries a tenant claim.",
    )

    // Self-heal a half-stored identity. An earlier version of bootstrap()
    // stripped identity params from the URL, so a browser that once loaded a
    // login-without-tenant URL is left holding a _lid with no _tid and no way
    // to recover by reloading — the stale login silently outlives every
    // attempt to fix it, including supplying a correct URL, because the
    // stored value is what resolve() reads on a bare reload. Dropping the
    // orphaned login here means the next load with real params starts clean,
    // and a bare reload fails as "not logged in" (honest and actionable)
    // rather than "logged in but permanently unauthorised".
    const urlHasIdentity = new URLSearchParams(window.location.search).size > 0
    if (!urlHasIdentity && readStore(LOGIN_KEY) && !readStore(TENANT_KEY)) {
      try {
        sessionStorage.removeItem(LOGIN_KEY)
        console.warn(
          '[auth:6.0] Cleared a stored login that had no tenant alongside it. ' +
          'Reload with ?loginId=<user>&tenantId=<id> to sign in again.',
        )
      } catch {
        /* Non-fatal: the warning above is still the actionable part. */
      }
    }
  }

  return {
    loginId:  loginId ?? '',
    tenantId: tenantId ?? '',
    uid:      firstClaim(claims, ['UserId', 'userId', 'uid']) ?? '',
    token,
  }
}
