// Build-time Content-Security-Policy handling for the shipped web.config (code review H-10).
//
// The committed web.config files carry a REPORT-ONLY policy with `frame-ancestors *`,
// because the real iDEAL host origin(s) are not known to this repository. At build time:
//   VITE_HOST_ORIGIN=https://ideal.example.com[,https://other.example.com]
//       narrows `frame-ancestors` to exactly those origins.
//   VITE_CSP_ENFORCE=true
//       renames the header from Content-Security-Policy-Report-Only to
//       Content-Security-Policy. Refused unless VITE_HOST_ORIGIN is set: enforcing
//       `frame-ancestors *` would claim protection it does not give.
// With neither set the file is returned unchanged (the previous behaviour).

const REPORT_ONLY = 'Content-Security-Policy-Report-Only'
const ENFORCING = 'Content-Security-Policy'

export function parseHostOrigins(raw) {
  const origins = String(raw ?? '').split(',').map((o) => o.trim()).filter(Boolean)
  return origins.map((o) => {
    let u
    try { u = new URL(o) } catch { throw new Error(`VITE_HOST_ORIGIN: "${o}" is not a URL`) }
    if (u.protocol !== 'https:' && u.protocol !== 'http:') {
      throw new Error(`VITE_HOST_ORIGIN: "${o}" must be an http(s) origin`)
    }
    if (u.origin !== o.replace(/\/$/, '')) {
      throw new Error(`VITE_HOST_ORIGIN: "${o}" must be an origin only (scheme://host[:port]), expected "${u.origin}"`)
    }
    return u.origin
  })
}

export function applyCsp(xml, { hostOrigin = '', enforce = false } = {}) {
  const origins = parseHostOrigins(hostOrigin)
  const wantEnforce = String(enforce).toLowerCase() === 'true'
  if (wantEnforce && !origins.length) {
    throw new Error('VITE_CSP_ENFORCE=true needs VITE_HOST_ORIGIN (refusing to enforce frame-ancestors *)')
  }
  let out = xml
  if (origins.length) {
    // Match the header VALUE (ends at the closing quote), not the comment that mentions it.
    const directive = /frame-ancestors \*(?=")/
    if (!directive.test(out)) throw new Error('web.config has no "frame-ancestors *" header value to narrow')
    out = out.replace(directive, `frame-ancestors ${origins.join(' ')}`)
  }
  if (wantEnforce) {
    const header = `name="${REPORT_ONLY}"`
    if (!out.includes(header)) throw new Error(`web.config has no ${REPORT_ONLY} header to enforce`)
    out = out.replace(header, `name="${ENFORCING}"`)
  }
  return out
}
