import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the Vite server proxies to the API container/process so the app
// always talks to a same-origin /api, exactly as it does behind nginx in production.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': { target: process.env.VITE_API_URL || 'http://localhost:8000', changeOrigin: true },
      '/media': { target: process.env.VITE_API_URL || 'http://localhost:8000', changeOrigin: true },
    },
  },
})
