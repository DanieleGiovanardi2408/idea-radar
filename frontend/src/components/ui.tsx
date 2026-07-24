import { useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { AnimatedNumber } from './motion'

/* ---- Colori di stato (riservati: mai riusati come "serie" decorative) ----- */

export const STATUS_STYLES: Record<string, string> = {
  proposed: 'bg-phosphor/10 text-phosphor ring-phosphor/30',
  processed: 'bg-signal/10 text-signal/90 ring-signal/25',
  archived: 'bg-slate-700/20 text-slate-500 ring-slate-700/40',
}

export const DIFFICULTY_STYLES: Record<string, string> = {
  low: 'bg-phosphor/10 text-phosphor ring-phosphor/30',
  med: 'bg-ember/10 text-ember ring-ember/30',
  high: 'bg-flare/10 text-flare ring-flare/30',
}

/* ---- Icone minime (niente librerie) --------------------------------------- */

export function IconArrowUpRight({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={`size-3.5 ${className}`} fill="none">
      <path
        d="M4.5 11.5 11.5 4.5M6 4.5h5.5V10"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function IconChevron({
  open,
  className = '',
}: {
  open?: boolean
  className?: string
}) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      className={`size-4 transition-transform duration-300 ${open ? 'rotate-180' : ''} ${className}`}
    >
      <path
        d="m4 6 4 4 4-4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function IconX({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={`size-4 ${className}`}>
      <path
        d="m4.5 4.5 7 7m0-7-7 7"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function IconPin({
  filled = false,
  className = '',
}: {
  filled?: boolean
  className?: string
}) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={`size-3.5 ${className}`}>
      <path
        d="M6 1.5h4l-.6 4.2 2.1 2.3v1.5H4.5V8l2.1-2.3L6 1.5Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        fill={filled ? 'currentColor' : 'none'}
      />
      <line
        x1="8"
        y1="9.5"
        x2="8"
        y2="14.5"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function IconDismiss({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={`size-3.5 ${className}`}>
      <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.3" />
      <line
        x1="4.6"
        y1="11.4"
        x2="11.4"
        y2="4.6"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function IconRestore({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={`size-3.5 ${className}`}>
      <path
        d="M3 8a5 5 0 1 0 1.5-3.6M3 2.5V5h2.5"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/* ---- Superfici ------------------------------------------------------------- */

export function Badge({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${className}`}
    >
      {children}
    </span>
  )
}

export function Panel({
  children,
  className = '',
  interactive = false,
}: {
  children: ReactNode
  className?: string
  interactive?: boolean
}) {
  return (
    <div className={`glass rounded-2xl ${interactive ? 'glass-hover' : ''} ${className}`}>
      {children}
    </div>
  )
}

/* ---- Marcatori di magnitudine (un solo hue-dato: fosforo) ------------------ */

export function MetricBar({
  label,
  value,
  hint,
  delayMs = 0,
}: {
  label: string
  value: number | null
  hint?: string
  delayMs?: number
}) {
  const pct = Math.round((value ?? 0) * 100)
  return (
    <div className="flex items-center gap-3" title={hint}>
      <span className="w-24 shrink-0 text-xs text-slate-400">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="grow-bar h-full rounded-full bg-gradient-to-r from-phosphor/45 to-phosphor shadow-[0_0_10px_-1px_var(--color-phosphor)]"
          style={{ width: `${pct}%`, animationDelay: `${delayMs}ms` }}
        />
      </div>
      <span className="w-7 text-right font-display text-xs font-semibold tabular-nums text-slate-300">
        {pct}
      </span>
    </div>
  )
}

export function scoreTone(value: number): string {
  if (value >= 0.5) return 'text-phosphor'
  if (value >= 0.35) return 'text-signal'
  return 'text-slate-600'
}

export function ScoreRing({ value, size = 48 }: { value: number; size?: number }) {
  const pct = Math.max(0, Math.min(1, value))
  const stroke = 3.5
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const glow = pct >= 0.5
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="stroke-white/[0.07]"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          className={`stroke-in stroke-current ${scoreTone(pct)} ${
            glow ? 'drop-shadow-[0_0_6px_rgba(46,232,162,0.55)]' : ''
          }`}
          style={
            {
              strokeDashoffset: circumference * (1 - pct),
              '--dash': circumference,
            } as CSSProperties
          }
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center font-display text-xs font-semibold tabular-nums text-slate-100">
        {Math.round(pct * 100)}
      </span>
    </div>
  )
}

/* ---- Grafico area con hover (un grafico vero ha sempre il tooltip) --------- */

const SPARK_TONES = {
  accent: { stroke: 'var(--color-phosphor)', id: 'phos' },
  up: { stroke: 'var(--color-phosphor)', id: 'phos' },
  down: { stroke: 'var(--color-flare)', id: 'flare' },
  flat: { stroke: 'rgba(146,180,210,0.55)', id: 'dim' },
} as const

export function AreaSpark({
  values,
  tone = 'accent',
  width = 220,
  height = 56,
  format,
}: {
  values: number[]
  tone?: keyof typeof SPARK_TONES
  width?: number
  height?: number
  format?: (value: number, index: number) => string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  if (values.length === 0) return null

  const pad = 4
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0
  const x = (i: number) => pad + i * step
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2)
  const line = values.map((v, i) => `${x(i)},${y(v)}`).join(' ')
  const area = `${pad},${height - pad} ${line} ${x(values.length - 1)},${height - pad}`
  const t = SPARK_TONES[tone]
  const gid = `spark-${t.id}`
  const approxLength = width * 1.4

  const onMove = (e: React.MouseEvent) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || values.length < 2) return
    const rel = ((e.clientX - rect.left) / rect.width) * width
    const index = Math.round((rel - pad) / step)
    setHover(Math.max(0, Math.min(values.length - 1, index)))
  }

  return (
    <div className="relative" style={{ width, height }}>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        className="overflow-visible"
      >
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={t.stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={t.stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={area} fill={`url(#${gid})`} />
        <polyline
          points={line}
          fill="none"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          stroke={t.stroke}
          strokeDasharray={approxLength}
          className="stroke-in"
          style={{ '--dash': approxLength } as CSSProperties}
        />
        {hover === null && (
          <circle
            cx={x(values.length - 1)}
            cy={y(values[values.length - 1])}
            r={2.8}
            fill={t.stroke}
            style={{ animation: 'pulse-soft 2.2s ease-in-out infinite' }}
          />
        )}
        {hover !== null && (
          <g>
            <line
              x1={x(hover)}
              y1={pad}
              x2={x(hover)}
              y2={height - pad}
              stroke="rgba(146,180,210,0.3)"
              strokeDasharray="2 3"
            />
            <circle cx={x(hover)} cy={y(values[hover])} r={3.4} fill={t.stroke} />
          </g>
        )}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute -top-7 z-10 -translate-x-1/2 rounded-md border border-white/10 bg-deep/95 px-2 py-0.5 font-display text-[11px] font-medium whitespace-nowrap tabular-nums text-slate-200 shadow-lg"
          style={{ left: `${(x(hover) / width) * 100}%` }}
        >
          {format ? format(values[hover], hover) : values[hover]}
        </div>
      )}
    </div>
  )
}

/* ---- Tessere numeriche ------------------------------------------------------ */

export function StatCard({
  label,
  value,
  hint,
  delayMs = 0,
}: {
  label: string
  value: number
  hint?: string
  delayMs?: number
}) {
  return (
    <div className="stagger" style={{ animationDelay: `${delayMs}ms` }}>
      <Panel className="p-4" interactive>
        <div className="hud text-slate-500">{label}</div>
        <div className="mt-1.5 font-display text-3xl font-semibold tracking-tight tabular-nums text-slate-50">
          <AnimatedNumber value={value} />
        </div>
        {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
      </Panel>
    </div>
  )
}

/* ---- Stati vuoti e scheletri ------------------------------------------------ */

function MiniRadar() {
  return (
    <svg viewBox="0 0 96 96" className="mx-auto size-20 text-phosphor/40">
      {[14, 27, 40].map((r) => (
        <circle
          key={r}
          cx="48"
          cy="48"
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth="1"
          opacity="0.5"
        />
      ))}
      <line x1="48" y1="8" x2="48" y2="88" stroke="currentColor" strokeWidth="0.6" opacity="0.25" />
      <line x1="8" y1="48" x2="88" y2="48" stroke="currentColor" strokeWidth="0.6" opacity="0.25" />
      <g style={{ transformOrigin: '48px 48px', animation: 'sweep-rotate 5s linear infinite' }}>
        <path d="M48 48 L48 8 A40 40 0 0 1 76 20 Z" fill="currentColor" opacity="0.16" />
        <line x1="48" y1="48" x2="48" y2="8" stroke="currentColor" strokeWidth="1.4" />
      </g>
      <circle cx="48" cy="48" r="2.4" fill="currentColor" />
    </svg>
  )
}

/* Errore di caricamento di una singola risorsa: ogni vista ha il suo. */
export function ErrorNotice({ children }: { children: ReactNode }) {
  return (
    <p className="view-enter rounded-xl border border-flare/25 bg-flare/5 px-3.5 py-2.5 text-sm text-flare">
      {children}
    </p>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="glass view-enter rounded-2xl p-12 text-center">
      <MiniRadar />
      <div className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-slate-400">
        {children}
      </div>
    </div>
  )
}

export function SkeletonCard() {
  return (
    <div className="glass relative overflow-hidden rounded-2xl p-4">
      <div className="flex items-start gap-4">
        <div className="size-12 rounded-full bg-white/[0.05]" />
        <div className="flex-1 space-y-2.5 py-1">
          <div className="h-3.5 w-1/2 rounded bg-white/[0.06]" />
          <div className="h-3 w-4/5 rounded bg-white/[0.04]" />
          <div className="h-3 w-1/3 rounded bg-white/[0.04]" />
        </div>
      </div>
      <div className="progress-sheen absolute inset-0 opacity-[0.15]" />
    </div>
  )
}
