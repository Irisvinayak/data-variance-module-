import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    proxy: {
      // Forward all /variance/* requests to the standalone backend on port 8002
      '/variance': {
        target:       'http://localhost:8002',
        changeOrigin: true,
      },
    },
  },
})
