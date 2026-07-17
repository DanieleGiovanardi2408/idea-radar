/* Il quadrante: le idee come blip su un radar polare.
 *
 * Geometria: distanza dal centro = 1 − composite (le opportunità migliori sono
 * "sulla nostra rotta", vicine al centro); l'angolo è un settore stabile per
 * topic con un offset deterministico per idea — la mappa non balla tra render.
 * La spazzata ruota con periodo fisso e ogni blip lampeggia quando gli passa
 * sopra: la sincronia è solo un animation-delay proporzionale all'angolo.
 */

import { useMemo, useState, type CSSProperties } from 'react'
import type { IdeaOut } from '../types'

const SIZE = 440
const CENTER = SIZE / 2
const R_MIN = 46
const R_MAX = 190
const SWEEP_SECONDS = 7
const MAX_BLIPS = 60

type Blip = {
  idea: IdeaOut
  x: number
  y: number
  angle: number
  proposed: boolean
}

function blipsFor(ideas: IdeaOut[]): Blip[] {
  const topicOrder = Array.from(
    new Set(ideas.map((i) => i.topic_id ?? -1)),
  ).sort((a, b) => a - b)
  const sector = 360 / Math.max(topicOrder.length, 1)

  return ideas
    .slice()
    .sort((a, b) => b.composite - a.composite)
    .slice(0, MAX_BLIPS)
    .map((idea) => {
      const sectorIndex = topicOrder.indexOf(idea.topic_id ?? -1)
      // Spirale aurea dentro il settore: sparpaglia senza casualità.
      const offset = sector * 0.08 + ((idea.id * 137.508) % (sector * 0.84))
      const angle = sectorIndex * sector + offset
      const radius = R_MIN + (1 - Math.max(0, Math.min(1, idea.composite))) * (R_MAX - R_MIN)
      const rad = ((angle - 90) * Math.PI) / 180
      return {
        idea,
        angle,
        x: CENTER + radius * Math.cos(rad),
        y: CENTER + radius * Math.sin(rad),
        proposed: idea.status === 'proposed',
      }
    })
}

export function RadarScope({
  ideas,
  onSelect,
}: {
  ideas: IdeaOut[]
  onSelect: (id: number) => void
}) {
  const blips = useMemo(() => blipsFor(ideas), [ideas])
  const [hover, setHover] = useState<Blip | null>(null)
  const proposedCount = ideas.filter((i) => i.status === 'proposed').length

  if (ideas.length === 0) return null

  return (
    <div className="glass view-enter relative overflow-hidden rounded-3xl">
      {/* HUD agli angoli */}
      <div className="hud pointer-events-none absolute top-4 left-5 z-10 flex items-center gap-2 text-phosphor/70">
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-phosphor opacity-60" />
          <span className="relative inline-flex size-1.5 rounded-full bg-phosphor" />
        </span>
        scansione attiva
      </div>
      <div className="hud pointer-events-none absolute top-4 right-5 z-10 text-slate-500">
        {ideas.length} contatti · {proposedCount} sopra soglia
      </div>

      <div className="mx-auto max-w-[560px] px-4 pt-6 pb-2">
        <div className="relative">
          <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="block w-full">
          <defs>
            <linearGradient id="sweep-fill" gradientUnits="userSpaceOnUse"
              x1={CENTER - 118} y1={CENTER - 145} x2={CENTER} y2={CENTER - 170}>
              <stop offset="0%" stopColor="var(--color-phosphor)" stopOpacity="0" />
              <stop offset="78%" stopColor="var(--color-phosphor)" stopOpacity="0.13" />
              <stop offset="100%" stopColor="var(--color-phosphor)" stopOpacity="0.3" />
            </linearGradient>
            <radialGradient id="scope-glow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="var(--color-phosphor)" stopOpacity="0.07" />
              <stop offset="70%" stopColor="var(--color-phosphor)" stopOpacity="0.02" />
              <stop offset="100%" stopColor="transparent" stopOpacity="0" />
            </radialGradient>
          </defs>

          {/* alone di fondo */}
          <circle cx={CENTER} cy={CENTER} r={R_MAX + 12} fill="url(#scope-glow)" />

          {/* anelli e assi */}
          {[R_MIN, 94, 142, R_MAX].map((r) => (
            <circle
              key={r}
              cx={CENTER}
              cy={CENTER}
              r={r}
              fill="none"
              stroke={r === R_MAX ? 'rgba(46,232,162,0.18)' : 'rgba(146,180,210,0.1)'}
              strokeWidth="1"
            />
          ))}
          <line x1={CENTER} y1={CENTER - R_MAX} x2={CENTER} y2={CENTER + R_MAX} stroke="rgba(146,180,210,0.07)" />
          <line x1={CENTER - R_MAX} y1={CENTER} x2={CENTER + R_MAX} y2={CENTER} stroke="rgba(146,180,210,0.07)" />

          {/* tacche sull'anello esterno */}
          {Array.from({ length: 24 }, (_, i) => i * 15).map((deg) => {
            const rad = ((deg - 90) * Math.PI) / 180
            const inner = deg % 90 === 0 ? R_MAX - 9 : R_MAX - 5
            return (
              <line
                key={deg}
                x1={CENTER + inner * Math.cos(rad)}
                y1={CENTER + inner * Math.sin(rad)}
                x2={CENTER + R_MAX * Math.cos(rad)}
                y2={CENTER + R_MAX * Math.sin(rad)}
                stroke="rgba(46,232,162,0.28)"
                strokeWidth="1"
              />
            )
          })}

          {/* spazzata */}
          <g
            style={{
              transformOrigin: `${CENTER}px ${CENTER}px`,
              animation: `sweep-rotate ${SWEEP_SECONDS}s linear infinite`,
            }}
          >
            <path
              d={`M${CENTER},${CENTER} L${CENTER - R_MAX * 0.5},${CENTER - R_MAX * 0.866} A${R_MAX},${R_MAX} 0 0 1 ${CENTER},${CENTER - R_MAX} Z`}
              fill="url(#sweep-fill)"
            />
            <line
              x1={CENTER}
              y1={CENTER}
              x2={CENTER}
              y2={CENTER - R_MAX}
              stroke="var(--color-phosphor)"
              strokeOpacity="0.75"
              strokeWidth="1.5"
              style={{ filter: 'drop-shadow(0 0 5px rgba(46,232,162,0.8))' }}
            />
          </g>

          {/* centro */}
          <circle cx={CENTER} cy={CENTER} r="3" fill="var(--color-phosphor)" />
          <circle
            cx={CENTER}
            cy={CENTER}
            r="7"
            fill="none"
            stroke="var(--color-phosphor)"
            strokeOpacity="0.35"
            style={{ animation: 'pulse-soft 2.4s ease-in-out infinite' }}
          />

          {/* blip */}
          {blips.map((blip) => (
            <g
              key={blip.idea.id}
              className="cursor-pointer"
              style={
                {
                  animation: `blip-flash ${SWEEP_SECONDS}s linear infinite`,
                  animationDelay: `${(blip.angle / 360) * SWEEP_SECONDS}s`,
                  '--blip-rest': blip.proposed ? 0.92 : 0.42,
                } as CSSProperties
              }
              onMouseEnter={() => setHover(blip)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect(blip.idea.id)}
            >
              {blip.proposed && (
                <circle
                  cx={blip.x}
                  cy={blip.y}
                  r="8.5"
                  fill="none"
                  stroke="var(--color-phosphor)"
                  strokeOpacity="0.35"
                  strokeWidth="1"
                />
              )}
              <circle
                cx={blip.x}
                cy={blip.y}
                r={blip.proposed ? 4.2 : 3}
                fill={blip.proposed ? 'var(--color-phosphor)' : 'var(--color-signal)'}
                style={{
                  filter: blip.proposed
                    ? 'drop-shadow(0 0 5px rgba(46,232,162,0.9))'
                    : 'drop-shadow(0 0 3px rgba(62,195,240,0.5))',
                }}
              />
              {/* area di click generosa (più grande del segno) */}
              <circle cx={blip.x} cy={blip.y} r="14" fill="transparent" />
            </g>
          ))}
        </svg>

        {/* tooltip */}
        {hover && (
          <div
            className="pointer-events-none absolute z-20 w-56 -translate-x-1/2 -translate-y-full rounded-xl border border-white/10 bg-deep/95 p-3 shadow-2xl backdrop-blur-md"
            style={{
              left: `${(hover.x / SIZE) * 100}%`,
              top: `${(hover.y / SIZE) * 100}%`,
              marginTop: -14,
            }}
          >
            <div className="line-clamp-2 text-xs font-medium text-slate-100">
              {hover.idea.label}
            </div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-[11px] text-slate-500">
                {hover.idea.topic_label ?? 'senza topic'}
              </span>
              <span className={`font-display text-xs font-semibold tabular-nums ${hover.proposed ? 'text-phosphor' : 'text-signal'}`}>
                {Math.round(hover.idea.composite * 100)}
              </span>
            </div>
          </div>
        )}
        </div>
      </div>

      {/* legenda: mai solo colore (il proposto ha anche l'anello) */}
      <div className="flex items-center justify-center gap-6 pb-4 text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-2">
          <span className="relative inline-flex size-3 items-center justify-center">
            <span className="absolute inset-0 rounded-full border border-phosphor/40" />
            <span className="size-1.5 rounded-full bg-phosphor" />
          </span>
          sopra soglia
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="size-1.5 rounded-full bg-signal/80" />
          in osservazione
        </span>
        <span className="text-slate-600">centro = punteggio alto</span>
      </div>
    </div>
  )
}
