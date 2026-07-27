// Screenshot puliti del frontend (senza barra del browser), per il README.
// Uso:  cd ~/idea-radar/_refactor_assets && node shot-mac.mjs ~/idea-radar/docs
// Il frontend deve girare su http://localhost:5173 con il backend attivo (dati veri).
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] ?? 'docs'
const URL = process.env.URL ?? 'http://localhost:5173'
mkdirSync(out, { recursive: true })
const log = (...a) => console.log('[shot]', ...a)

// La sala controllo del Radar vive dietro il breakpoint xl (1280px) e il suo
// contenitore arriva a 92rem: sotto i ~1500px le tre colonne si stringono, e
// l'altezza serve tutta perché il quadrante è quadrato e largo quanto la
// colonna centrale. Le altre viste restano su max-w-5xl, quindi una finestra
// larga aggiunge solo sfondo.
const CONTROL_ROOM = { width: 1600, height: 1240 }
const LIST_VIEWS = { width: 1500, height: 1000 }

let browser
try {
  browser = await chromium.launch()
} catch (e) {
  console.error('\n❌ Chromium non trovato. Esegui:  npx playwright install chromium\n')
  console.error(String(e.message || e))
  process.exit(1)
}

const page = await browser.newPage({
  viewport: CONTROL_ROOM,
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

/* I tab della nav sono link (React Router), non bottoni: cercarli come
   `role=button` non li trova e ogni vista finiva per essere uno scatto del
   Radar. Si prova il ruolo giusto e si ripiega sull'altro, così lo script
   sopravvive anche se domani tornano bottoni. Il click è forzato per saltare i
   controlli di actionability, che con le animazioni di ingresso fanno timeout. */
const clickTab = async (rx) => {
  for (const role of ['link', 'button']) {
    const el = page.getByRole(role, { name: rx }).first()
    if (await el.count()) {
      await el.click({ force: true, timeout: 8000 })
      return
    }
  }
  throw new Error(`tab non trovato: ${rx}`)
}

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

/* Il pannello video parte con un player in autoplay muto. In uno screenshot è
   una variabile impazzita: il Chromium di Playwright non ha i codec proprietari
   e YouTube, dentro un headless, può mostrare uno spinner, un errore o un
   fotogramma nero a caso. Spegnendo il player si ottiene lo stato deterministico
   — miniatura del video in scena più la lista — e il README smette di dipendere
   da cosa decide YouTube nel momento dello scatto. */
const stopPlayer = async () => {
  const stop = page.getByRole('button', { name: /chiudi il player/i }).first()
  if (await stop.count()) {
    await stop.click({ force: true, timeout: 4000 }).catch(() => {})
    await page.waitForTimeout(400)
  }
}

await stopPlayer()
await shot('radar')

// Ritagli dei due pannelli laterali: nella hero, larga 900px, i dettagli della
// heatmap si perdono, quindi il README li mostra anche da vicino.
const panel = async (name, index) => {
  try {
    const el = page.locator('main aside').nth(index).locator(':scope > div').first()
    await el.screenshot({ path: `${out}/${name}.png` })
    log('✓', `${name}.png`)
  } catch (e) {
    log('✗', name, '—', String(e.message || e).slice(0, 200))
  }
}
await panel('panel-videos', 0)
await panel('panel-rhythm', 1)

// Le altre viste sono liste: finestra più bassa, scatto a pagina intera.
await page.setViewportSize(LIST_VIEWS)
await shot('topics', () => clickTab(/^Topic/), { fullPage: true })
await shot('trends', () => clickTab(/^Trend/), { fullPage: true })
await shot('monitor', () => clickTab(/^Monitor/), { fullPage: true })
await shot('detail', async () => {
  await clickTab(/^Radar/)
  await page.waitForTimeout(700)
  await stopPlayer() // anche qui: il drawer si apre sopra il Radar
  await page.locator('[data-testid="idea-card"]').first().click({ force: true, timeout: 8000 })
})

await browser.close()
log('fatto ->', out)
