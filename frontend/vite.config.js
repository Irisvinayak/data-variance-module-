import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')

  return {
    plugins: [react()],

    // IMPORTANT: IIS Virtual Directory
    base: '/DataVar/',

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
      proxy: Object.fromEntries(
        ['/variance', '/auth', '/health', '/app-config', '/docs', '/redoc', '/openapi.json']
          .map((route) => [
            route,
            {
              target: env.VITE_PROXY_TARGET || 'http://localhost:8000',
              changeOrigin: true,
            },
          ]),
      ),
    },
  }
})