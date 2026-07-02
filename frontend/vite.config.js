import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const envDir = __dirname
  const env = loadEnv(mode, envDir, '')

  const appBasePath = (env.VITE_BASE_PATH || './').trim() || './'
  const proxyTarget = (env.VITE_PROXY_TARGET || 'http://localhost:8000').trim()
  const devPort = Number(env.VITE_PORT || 3001)

  return {
    envDir,
    plugins: [react()],
    base: appBasePath,

    server: {
      port: devPort,
      proxy: {
        '/variance': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/auth': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/health': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})