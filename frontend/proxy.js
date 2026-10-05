// Proxy tables for the Vite dev server and `vite preview` (code review M-26).
//
// Dev serves the page from "/" and calls the API at "/variance/..." so the plain route
// prefixes are proxied. `vite preview` serves the PRODUCTION bundle, whose API base is
// VITE_API_BASE_URL (e.g. /DataVar6.0/api, the IIS rewrite prefix): those calls need the
// same prefix stripped, otherwise preview answers them with index.html and the page
// shows "non-JSON response" errors that look like a broken backend.

export const BACKEND_ROUTES = ['/variance', '/auth', '/health', '/app-config', '/docs', '/redoc', '/openapi.json']

export function devProxy(target) {
  return Object.fromEntries(BACKEND_ROUTES.map((route) => [route, { target, changeOrigin: true }]))
}

const REGEX_SPECIALS = '.*+?^${}()|[]\\/'

function escapeRegex(text) {
  return Array.from(text, (ch) => (REGEX_SPECIALS.includes(ch) ? `\\${ch}` : ch)).join('')
}

export function previewProxy(target, apiBase = '') {
  const base = String(apiBase || '').trim().replace(/\/+$/, '')
  if (!base || !base.startsWith('/')) return devProxy(target)   // absolute or empty base: same as dev
  return {
    [`^${escapeRegex(base)}/`]: {
      target,
      changeOrigin: true,
      rewrite: (path) => path.slice(base.length),
    },
  }
}
