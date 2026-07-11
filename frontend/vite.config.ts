import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Inoltra /health al backend FastAPI (evita problemi di CORS in dev)
      '/health': process.env.BACKEND_URL ?? 'http://localhost:8000',
    },
  },
})
