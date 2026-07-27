import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] ?? 'out'
mkdirSync(out, { recursive: true })

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
})
const page = await browser.newPage({
  viewport: { width: 1440, height: 960 },
  deviceScaleFactor: 2,
})
await page.goto('http://localhost:5173', { waitUntil: 'networkidle' })
await page.waitForTimeout(1600)
await page.screenshot({ path: `${out}/1-radar.png` })

for (const [file, label] of [
  ['2-topics', /^Topic/],
  ['3-trends', /^Trend/],
  ['4-monitor', /^Monitor/],
]) {
  await page.getByRole('button', { name: label }).first().click()
  await page.waitForTimeout(1100)
  await page.screenshot({ path: `${out}/${file}.png` })
}

await page.getByRole('button', { name: /^Radar/ }).first().click()
await page.waitForTimeout(700)
await page.getByText('Agente AI che ripara le CI rotte').first().click()
await page.waitForTimeout(1100)
await page.screenshot({ path: `${out}/5-detail.png` })

await browser.close()
console.log('shots ->', out)
