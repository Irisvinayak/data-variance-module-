import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { applyCsp } from './csp.js'
import { devProxy, previewProxy } from './proxy.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// The dev proxy target follows the SAME switch as the backend: VERSION in the
// root .env. Hardcoding a port here (or pinning one in frontend/.env) means
// flipping VERSION silently points the proxy at a dead port, and every request
// fails with ECONNREFUSED — which reads like the backend is down rather than
// like a config mismatch. Precedence mirrors backend/config/settings.py
// exactly (DV_SERVER_PORT_<v> -> DV_SERVER_PORT -> default) so the two cannot
// drift. VITE_PROXY_TARGET still wins when set, for pointing dev at a backend
// on another host.
function backendTarget(mode) {
  const root = loadEnv(mode, path.resolve(__dirname, '..'), '')
  const isLegacy = (root.VERSION || '5.5').trim().startsWith('5')
  const port =
    (isLegacy ? root.DV_SERVER_PORT_55 : root.DV_SERVER_PORT_60) ||
    root.DV_SERVER_PORT ||
    (isLegacy ? '8004' : '8003')
  return `http://localhost:${String(port).trim()}`
}

// Ships exactly one web.config per build. public/ is copied verbatim, so both
// web.config (5.5) and web.60.config (6.0) used to land in every dist/ and a
// 6.0 deploy needed a manual rename. production55 -> keep the 5.5 file; any
// other mode -> the 6.0 file becomes web.config. web.60.config never ships.
function webConfigPerBuild(mode, env = {}) {
  let outDir = ''
  return {
    name: 'dv-web-config-per-build',
    apply: 'build',
    configResolved(cfg) {
      outDir = path.resolve(cfg.root, cfg.build.outDir)
    },
    closeBundle() {
      const six0 = path.join(outDir, 'web.60.config')
      if (mode !== 'production55') {
        fs.copyFileSync(path.resolve(__dirname, 'public/web.60.config'), path.join(outDir, 'web.config'))
      }
      fs.rmSync(six0, { force: true })
      // H-10: narrow frame-ancestors / enforce the CSP when the host origin is known.
      const shipped = path.join(outDir, 'web.config')
      if (fs.existsSync(shipped)) {
        const xml = fs.readFileSync(shipped, 'utf8')
        const next = applyCsp(xml, { hostOrigin: env.VITE_HOST_ORIGIN, enforce: env.VITE_CSP_ENFORCE })
        if (next !== xml) fs.writeFileSync(shipped, next)
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const target = env.VITE_PROXY_TARGET || backendTarget(mode)

  return {
    plugins: [react(), webConfigPerBuild(mode, env)],

    // IMPORTANT: IIS Virtual Directory. Driven by VITE_BASE_PATH (see
    // frontend/.env.production) so a differently-named IIS site/vdir — e.g.
    // running 5.5 and 6.0 side by side under /DataVar6.0/ vs /DataVar55/ —
    // needs only an env change, not an edit here. './' (dev default) keeps
    // asset URLs relative for `vite dev`/`preview`.
    base: env.VITE_BASE_PATH || './',

    server: {
      port: Number(env.VITE_PORT || 3001),

      // EVERY backend route prefix must be listed here, or the dev server
      // answers it with the SPA's own 404/index.html instead of forwarding to
      // FastAPI — and the frontend sees a plausible-looking non-JSON response
      // rather than a connection error. That failure is silent by nature:
      // /app-config was missing from this list, so version detection fell back
      // to guessing from the URL shape and defaulted to 5.5 while the backend
      // was running 6.0, which surfaced only as confusing "loginId/tenantId
      // required" 401s several layers away.
      proxy: devProxy(target),
    },

    // `vite preview` serves the production bundle: its API base is VITE_API_BASE_URL.
    preview: {
      port: Number(env.VITE_PREVIEW_PORT || 4173),
      proxy: previewProxy(target, env.VITE_API_BASE_URL),
    },
  }
})