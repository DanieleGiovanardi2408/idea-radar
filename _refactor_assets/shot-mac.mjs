// Screenshot puliti del frontend (senza barra del browser), per il README.
// Uso:  cd ~/idea-radar/_refactor_assets && node shot-mac.mjs ~/idea-radar/docs
// Il frontend deve girare su http://localhost:5173 con il backend attivo (dati veri).
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] ?? 'docs'
const URL = process.env.URL ?? 'http://localhost:5173'
mkdirSync(out, { recursive: true })
const log = (...a) => console.log('[shot]', ...a)

let browser
try {
  browser = await chromium.launch()
} catch (e) {
  console.error('\n❌ Chromium non trovato. Esegui:  npx playwright install chromium\n')
  console.error(String(e.message || e))
  process.exit(1)
}

const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 2, // Retina: immagini nitide
})
page.setDefaultTimeout(15000)

try {
  log('apro', URL)
  await page.goto(URL, { waitUntil: 'load', timeout: 20000 })
} catch {
  console.error(`\n❌ Non raggiungo ${URL}. Il frontend è avviato con "npm run dev"?\n`)
  await browser.close()
  process.exit(1)
}

await page.waitForSelector('nav', { timeout: 15000 }).catch(() => {})
await page.waitForTimeout(1800) // sweep + count-up + font

// Localizza col motore di Playwright (affidabile) e forza il click (salta l'actionability).
const clickTab = (rx) =>
  page.getByRole('button', { name: rx }).first().click({ force: true, timeout: 8000 })

const shot = async (name, fn, opts = {}) => {
  try {
    if (fn) await fn()
    await page.waitForTimeout(1200) // lascia finire fade-up + stagger
    await page.screenshot({ path: `${out}/${name}.png`, ...opts })
    log('✓', `${name}.png`)
  } catch (e) {
    log('✗', name, '—', String(e.message || e).slice(0, 200))
  }
}

await shot('radar')
await shot('topics', () => clickTab(/^Topic/), { fullPage: true })
await shot('trends', () => clickTab(/^Trend/), { fullPage: true })
await shot('monitor', () => clickTab(/^Monitor/), { fullPage: true })
await shot('detail', async () => {
  await clickTab(/^Radar/)
  await page.waitForTimeout(700)
  await page.locator('[data-testid="idea-card"]').first().click({ force: true, timeout: 8000 })
})

await browser.close()
log('fatto ->', out)
