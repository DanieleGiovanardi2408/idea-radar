import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Inoltra le rotte del backend FastAPI (evita problemi di CORS in dev)
      '/health': process.env.BACKEND_URL ?? 'http://localhost:8000',
      '/ideas': process.env.BACKEND_URL ?? 'http://localhost:8000',
      '/runs': process.env.BACKEND_URL ?? 'http://localhost:8000',
      '/topics': process.env.BACKEND_URL ?? 'http://localhost:8000',
      '/trends': process.env.BACKEND_URL ?? 'http://localhost:8000',
      '/stats': process.env.BACKEND_URL ?? 'http://localhost:8000',
    },
  },
})
