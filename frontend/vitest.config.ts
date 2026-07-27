/* Config separata da vite.config.ts di proposito: `vitest/config` porta con sé i
 * tipi di Vite 5 e questo progetto è su Vite 8, quindi mettere il blocco `test`
 * nella config principale fa litigare i due `UserConfig` sotto `tsc`. Qui i tipi
 * di vitest restano confinati a un file che il build non guarda. */
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
