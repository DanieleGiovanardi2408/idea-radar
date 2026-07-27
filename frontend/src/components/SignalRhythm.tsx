/* Il ritmo dei segnali: quando NASCE ciò che il radar intercetta.
 *
 * Su `created_at`, non su `fetched_at`. La differenza non è un dettaglio: il
 * secondo disegnerebbe una riga verticale ogni quattro ore — il ritmo del nostro
 * scheduler — invece di quello della rete.
 *
 * Nessuna libreria di grafici: 168 celle sono una griglia CSS.
 *
 * La griglia è TRASPOSTA rispetto a come si disegna di solito una heatmap
 * settimanale (giorni in riga, ore in colonna): qui i giorni sono 7 colonne e le
 * ore 24 righe. In una colonna laterale stretta e alta è l'unico orientamento che
 * dà celle grandi — 24 colonne in 400px fanno quadratini da 14px, illeggibili,
 * mentre 7 colonne ne fanno da 50px e le 24 righe riempiono l'altezza. */

import { useState } from 'react'
import { Panel } from './ui'
import { useRhythm } from '../hooks/useRadarData'

const DAYS = ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom']
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const WINDOWS = [7, 28, 90]

/** Scala non lineare: con un picco molto più alto della media, una scala
 *  lineare spegne tutte le celle tranne una. Ma la radice quadrata è troppo
 *  generosa — schiaccia tutto in un muro di verde uguale, che è l'errore
 *  opposto: 0.7 è il compromesso misurato sui dati reali (picco 42/ora, media
 *  intorno a 10), dove le ore morte restano scure e i picchi si vedono. */
function intensity(value: number, peak: number): number {
  if (value <= 0 || peak <= 0) return 0
  return Math.pow(value / peak, 0.7)
}

function fill(value: number, peak: number): string {
  const alpha = intensity(value, peak)
  return alpha > 0
    ? `rgba(46, 232, 162, ${0.05 + alpha * 0.95})`
    : 'rgba(255, 255, 255, 0.025)'
}

/** Lunedì = 0, come le righe che arrivano dal backend. */
function nowUtc(): { d: number; h: number } {
  const now = new Date()
  return { d: (now.getUTCDay() + 6) % 7, h: now.getUTCHours() }
}

export function SignalRhythm() {
  const [days, setDays] = useState(28)
  const [hovered, setHovered] = useState<{ d: number; h: number } | null>(null)
  const { data, isPending, isError } = useRhythm(days)

  const cell = hovered ? data?.grid[hovered.d][hovered.h] ?? 0 : null
  const here = nowUtc()

  // Totale per giorno: la riga di barre sotto la griglia. Dice in un colpo
  // d'occhio se la settimana ha un baricentro (lavorativo, o di weekend).
  const perDay = data ? data.grid.map((row) => row.reduce((a, b) => a + b, 0)) : []
  const dayPeak = perDay.length ? Math.max(...perDay) : 0

  return (
    <Panel className="flex flex-col p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="hud text-slate-500">Ritmo dei segnali</h3>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setDays(w)}
              aria-pressed={days === w}
              className={`rounded px-1.5 py-0.5 text-[10px] tabular-nums transition-colors ${
                days === w
                  ? 'bg-phosphor/15 text-phosphor'
                  : 'text-slate-600 hover:text-slate-400'
              }`}
            >
              {w}g
            </button>
          ))}
        </div>
      </div>

      {isError && <p className="mt-3 text-xs text-flare">Ritmo non disponibile.</p>}
      {isPending && !data && (
        <p className="mt-3 text-xs text-slate-600">Misurazione…</p>
      )}

      {data && (
        <>
          {/* Una riga di intestazione con i giorni, poi 24 righe di ore.
              `auto` per la colonna delle ore, i 7 giorni si spartiscono il resto. */}
          <div className="mt-3 grid grid-cols-[1.6rem_repeat(7,minmax(0,1fr))] gap-x-1">
            <span />
            {DAYS.map((label, d) => (
              <span
                key={label}
                className={`pb-1 text-center text-[10px] font-medium transition-colors ${
                  hovered?.d === d
                    ? 'text-phosphor'
                    : here.d === d
                      ? 'text-slate-300'
                      : 'text-slate-600'
                }`}
              >
                {label}
              </span>
            ))}

            {HOURS.map((h) => (
              <div key={h} className="contents">
                <span
                  className={`pr-1 text-right text-[9px] leading-4 tabular-nums transition-colors ${
                    hovered?.h === h
                      ? 'text-phosphor'
                      : h % 6 === 0
                        ? 'text-slate-500'
                        : 'text-slate-700/70'
                  }`}
                >
                  {String(h).padStart(2, '0')}
                </span>
                {DAYS.map((_, d) => {
                  const value = data.grid[d][h]
                  const active = hovered?.d === d && hovered?.h === h
                  const isNow = here.d === d && here.h === h
                  const isPeak = data.peak > 0 && value === data.peak
                  return (
                    <button
                      key={d}
                      type="button"
                      onMouseEnter={() => setHovered({ d, h })}
                      onFocus={() => setHovered({ d, h })}
                      onMouseLeave={() => setHovered(null)}
                      onBlur={() => setHovered(null)}
                      aria-label={`${DAYS[d]} ore ${h}: ${value} segnali`}
                      title={`${DAYS[d]} ${String(h).padStart(2, '0')}:00 — ${value} segnali`}
                      className={`mb-px h-[15px] rounded-[2px] transition-all duration-150 ${
                        active
                          ? 'z-10 ring-2 ring-phosphor'
                          : isPeak
                            ? 'ring-1 ring-phosphor/70'
                            : isNow
                              ? 'ring-1 ring-slate-500/60'
                              : ''
                      }`}
                      style={{ backgroundColor: fill(value, data.peak) }}
                    />
                  )
                })}
              </div>
            ))}

            {/* Totali per giorno: barre, non numeri — servono a confrontare. */}
            <span className="pt-1.5 pr-1 text-right text-[8px] leading-none text-slate-700">
              tot
            </span>
            {perDay.map((total, d) => (
              <div
                key={d}
                title={`${DAYS[d]}: ${total} segnali`}
                className="flex h-6 items-end pt-1.5"
              >
                <div
                  className="w-full rounded-[2px] bg-phosphor/35 transition-all duration-300"
                  style={{
                    height: `${dayPeak > 0 ? Math.max(6, (total / dayPeak) * 100) : 0}%`,
                  }}
                />
              </div>
            ))}
          </div>

          <div className="mt-3 flex items-center justify-between gap-2 text-[11px]">
            <span className="min-w-0 truncate text-slate-500">
              {cell !== null && hovered ? (
                <span className="text-phosphor">
                  {DAYS[hovered.d]} {String(hovered.h).padStart(2, '0')}:00 · {cell}{' '}
                  segnali
                </span>
              ) : (
                <>
                  {data.n_items} segnali · picco {data.peak}/ora
                </>
              )}
            </span>
            {/* Legenda: senza, i verdi sono decorazione. */}
            <span className="flex shrink-0 items-center gap-1 text-slate-700">
              <span className="text-[9px]">0</span>
              {[0, 0.25, 0.5, 0.75, 1].map((step) => (
                <span
                  key={step}
                  className="size-2 rounded-[1px]"
                  style={{ backgroundColor: fill(step * data.peak, data.peak) }}
                />
              ))}
              <span className="text-[9px] tabular-nums">{data.peak}</span>
            </span>
          </div>

          <div className="mt-1 flex items-baseline justify-between gap-2 text-[10px] text-slate-700">
            {data.n_without_date > 0 ? (
              /* Onestà: gli item senza data non finiscono in una cella inventata. */
              <span>{data.n_without_date} segnali senza data, esclusi</span>
            ) : (
              <span />
            )}
            <span>ore UTC</span>
          </div>
        </>
      )}
    </Panel>
  )
}
