/**
 * iDEAL 5.5 identity strategy.
 *
 * The MVC host builds the iframe/redirect URL itself and appends the login as a
 * plain query parameter:
 *
 *   http://localhost:3001?loginId=iris810&uid=104&aspSession=xyz
 *
 * There is no tenant in 5.5, so tenantId is always ''. Nothing is stored: the
 * host re-supplies the parameters on every navigation, and keeping a stale copy
 * in sessionStorage would outlive the host's own session.
 */

export const VERSION = '5.5'

export function detect() {
  const params = new URLSearchParams(window.location.search)
  return params.has('loginId')
}

export function resolve() {
  const params = new URLSearchParams(window.location.search)
  return {
    loginId:  params.get('loginId') || '',
    tenantId: '',
    uid:      params.get('uid') || '',
  }
}
