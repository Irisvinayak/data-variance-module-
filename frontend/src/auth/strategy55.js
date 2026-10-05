/**
 * iDEAL 5.5 identity strategy.
 *
 * The MVC host builds the iframe/redirect URL itself and appends the login as a
 * plain query parameter:
 *
 *   http://localhost:3001?loginId=iris810
 *
 * Session credentials (the session cookie value, the session id) are NOT part of
 * this URL and are never read: an older host that still sends `aspSession` or
 * `uid` has them removed from the address bar by auth/urlScrub.js before this
 * strategy runs.
 *
 * There is no tenant in 5.5, so tenantId is always ''. Nothing is stored: the
 * host re-supplies the parameters on every navigation, and keeping a stale copy
 * in sessionStorage would outlive the host's own session.
 */

export function detect() {
  const params = new URLSearchParams(window.location.search)
  return params.has('loginId')
}

export function resolve() {
  const params = new URLSearchParams(window.location.search)
  return {
    loginId:  params.get('loginId') || '',
    tenantId: '',
    uid:      '',
  }
}
