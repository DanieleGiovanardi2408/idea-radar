// Screenshot puliti del frontend (senza barra del browser), per il README.
// Uso:  cd ~/idea-radar/_refactor_assets && node shot-mac.mjs ~/idea-radar/docs
// Il frontend deve girare su http://localhost:5173 con il backend attivo (dati veri).
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync, appendFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const out = process.argv[2] ?? 'docs'
const URL = process.env.URL ?? 'http://localhost:5173'
mkdirSync(out, { recursive: true })

// Il log finisce anche su file: quando uno scatto esce sbagliato serve rileggere
// cosa diceva la pagina, non ricordarselo.
// `URL` qui sopra è la costante con l'indirizzo del frontend e maschera il
// costruttore globale: il percorso del log si costruisce senza passare da lì.
const LOG = join(dirname(fileURLToPath(import.meta.url)), 'shot.log')
writeFileSync(LOG, `# ${new Date().toISOString()} — ${URL}\n`)
const log = (...a) => {
  const riga = a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' ')
  console.log('[shot]', riga)
  appendFileSync(LOG, riga + '\n')
}

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

/* Uno screenshot non fallisce: salva quello che c'è. La prima versione della
   sala controllo è finita nel README con la colonna centrale vuota — quadrante
   e lista semplicemente assenti — e nessuno se ne è accorto perché il log
   diceva "✓". Da qui in poi gli errori della pagina si vedono. */
page.on('pageerror', (e) => log('⚠ errore JS:', String(e.message || e).slice(0, 300)))
page.on('console', (m) => {
  if (m.type() === 'error') log('⚠ console:', m.text().slice(0, 300))
})
page.on('requestfailed', (r) => log('⚠ richiesta fallita:', r.url().slice(0, 120)))

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

/* Niente `fullPage`, per due motivi che sono lo stesso motivo. Queste viste sono
   elenchi lunghi: la Topic a pagina intera veniva 3000×49508, un nastro 1:16 che
   nel README, largo 420px, non si legge; e la Trend non veniva affatto, perché
   Chromium non sa allocare una texture oltre i 16384px e con
   deviceScaleFactor: 2 il tetto reale è 8192px di pagina. Lo scatto è quindi il
   viewport, dall'alto: è la parte che racconta la vista, con un rapporto che sta
   in una tabella. */
const shot = async (name, fn, opts = {}) => {
  try {
    if (fn) await fn()
    await page.waitForTimeout(1200) // lascia finire fade-up + stagger
    await page.evaluate(() => window.scrollTo(0, 0))
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

/* La hero è l'unica immagine che deve mostrare tre colonne piene: se la centrale
   (quadrante + lista) è vuota, lo screenshot è carta straccia e va detto, non
   salvato in silenzio. */
const controllaColonne = async () => {
  const misure = await page.evaluate(() => {
    const grid = document.querySelector('main .view-enter > *')
    if (!grid) return { errore: 'griglia non trovata' }
    return {
      viewport: [innerWidth, innerHeight],
      colonne: [...grid.children].map((el) => ({
        alt: Math.round(el.getBoundingClientRect().height),
        larg: Math.round(el.getBoundingClientRect().width),
        x: Math.round(el.getBoundingClientRect().x),
        testo: (el.textContent || '').trim().slice(0, 30),
      })),
    }
  })
  log('colonne:', JSON.stringify(misure))
  /* L'altezza non basta come sentinella: la colonna vuota veniva comunque alta
     540px, tirata dalla riga della griglia. Quello che mancava era il contenuto,
     quindi si guarda il testo. */
  const centrale = misure.colonne?.[1]
  if (centrale && centrale.testo.trim().length < 10) {
    log('⚠ colonna centrale vuota o collassata — la hero non è utilizzabile')
    log('dump:', await page.evaluate(() => {
      const g = document.querySelector('main .view-enter > *')
      const c = g?.children[1]
      return c ? c.outerHTML.replace(/\s+/g, ' ').slice(0, 700) : 'colonna assente'
    }))
  }
}

await stopPlayer()
await controllaColonne()
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

// Le altre viste stanno su max-w-5xl: finestra più bassa, una larga aggiunge solo sfondo.
await page.setViewportSize(LIST_VIEWS)
await shot('topics', () => clickTab(/^Topic/))
await shot('trends', () => clickTab(/^Trend/))
await shot('monitor', () => clickTab(/^Monitor/))
await shot('detail', async () => {
  await clickTab(/^Radar/)
  await page.waitForTimeout(700)
  await stopPlayer() // anche qui: il drawer si apre sopra il Radar
  await page.locator('[data-testid="idea-card"]').first().click({ force: true, timeout: 8000 })
})

await browser.close()
log('fatto ->', out)
