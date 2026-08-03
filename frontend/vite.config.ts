import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backend = process.env.BACKEND_URL ?? 'http://localhost:8000'

// Le rotte del backend FastAPI da inoltrare in dev (evita problemi di CORS).
// Elencarle a mano è un rischio: aggiungere un endpoint e dimenticare questa
// riga significa un 404 che sembra un bug del frontend. `/profiles` era stato
// dimenticato esattamente così.
const BACKEND_ROUTES = [
  '/health',
  '/ideas',
  '/outcomes',
  '/profiles',
  '/rhythm',
  '/runs',
  '/topics',
  '/trends',
  '/videos',
  '/stats',
  '/workspace',
  '/yt',
]

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(BACKEND_ROUTES.map((route) => [route, backend])),
  },
})
