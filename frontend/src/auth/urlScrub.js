/**
 * Remove session/credential-looking parameters from the address bar.
 *
 * The 5.5 host used to build this module's URL with the user's session cookie
 * value (aspSession) and session id (uid); other hosts or integrations may put a
 * token there. None of these may ever be used as authentication, and none may
 * stay in the address bar, where they land in browser history, proxy and IIS logs
 * and the Referer header of every later request (code review C-01).
 *
 * Only these names are removed; loginId, lang and everything else are untouched.
 * `_at` is deliberately NOT here: strategy60.bootstrap() moves it into memory and
 * strips it itself, and must be able to read it first.
 */

export const CREDENTIAL_PARAMS = [
  'aspSession', 'aspsession', 'uid',
  'token', 'jwt', 'access_token', 'accessToken',
  'session', 'sessionId', 'sessionid',
]

/**
 * Strip the credential parameters from `location`'s URL via `history`.
 * Returns the names that were removed (empty when there was nothing to do).
 */
export function scrubUrlCredentials(loc = window.location, hist = window.history) {
  const params = new URLSearchParams(loc.search)
  const removed = CREDENTIAL_PARAMS.filter((name) => params.has(name))
  if (!removed.length) return []

  removed.forEach((name) => params.delete(name))
  const query = params.toString()
  try {
    hist.replaceState({}, '', `${loc.pathname}${query ? `?${query}` : ''}${loc.hash || ''}`)
  } catch { /* a restricted frame may refuse; the values are still never used */ }
  return removed
}
