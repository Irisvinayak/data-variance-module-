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
 * falling back to _lid when the token cannot be decoded.
 *
 * SECURITY: decoding here is for convenience only — it tells the UI who it is
 * talking about. It is NOT authentication. The backend must verify the token's
 * signature itself; see INTEGRATION_PLAN.md section 4. Until it does, these
 * claims are self-asserted and the API is open to anyone who supplies a
 * loginId and tenantId directly.
 */

export const VERSION = '6.0'

const TOKEN_KEY = '_at'
const LOGIN_KEY = '_lid'

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
  return params.has(TOKEN_KEY) || params.has(LOGIN_KEY) || Boolean(readStore(TOKEN_KEY))
}

/** Move _at/_lid out of the URL and into sessionStorage. Idempotent. */
export function bootstrap() {
  const params = new URLSearchParams(window.location.search)
  const token = params.get(TOKEN_KEY)
  const lid = params.get(LOGIN_KEY)

  if (token) writeStore(TOKEN_KEY, token)
  if (lid) writeStore(LOGIN_KEY, lid)

  if ((token || lid) && window.history.replaceState) {
    params.delete(TOKEN_KEY)
    params.delete(LOGIN_KEY)
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
    // A malformed token is not fatal — _lid still identifies the user, and the
    // backend is the authority on whether the request is allowed.
    return null
  }
}

export function resolve() {
  bootstrap()
  const token = readStore(TOKEN_KEY)
  const claims = token ? decodeJwtPayload(token) : null

  return {
    loginId:  claims?.LoginId ?? readStore(LOGIN_KEY),
    tenantId: claims?.TenantId ?? '',
    uid:      claims?.UserId ?? '',
    token,
  }
}
