import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],

  server: {
    port: 3001,   // React dev server — this is what .NET opens in the iframe

    proxy: {
      // Any request starting with /variance or /auth is forwarded to FastAPI
      // on port 8002. Vite adds the correct host header automatically.
      '/variance': {
        target:      'http://localhost:8002',
        changeOrigin: true,
      },
      '/auth': {
        target:      'http://localhost:8002',
        changeOrigin: true,
      },
      '/health': {
        target:      'http://localhost:8002',
        changeOrigin: true,
      },
    },
  },
})