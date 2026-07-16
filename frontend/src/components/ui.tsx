import type { ReactNode } from 'react'

export const STATUS_STYLES: Record<string, string> = {
  proposed: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  processed: 'bg-slate-500/10 text-slate-400 ring-slate-600/30',
  archived: 'bg-slate-700/20 text-slate-500 ring-slate-700/30',
}

export const DIFFICULTY_STYLES: Record<string, string> = {
  low: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  med: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  high: 'bg-rose-500/10 text-rose-300 ring-rose-500/30',
}

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
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={`rounded-2xl border border-slate-800/80 bg-slate-900/40 ${className}`}
    >
      {children}
    </div>
  )
}

export function MetricBar({
  label,
  value,
  hint,
}: {
  label: string
  value: number | null
  hint?: string
}) {
  const pct = Math.round((value ?? 0) * 100)
  return (
    <div className="flex items-center gap-3" title={hint}>
      <span className="w-24 shrink-0 text-xs text-slate-500">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-sky-500 to-cyan-400 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-7 text-right text-xs tabular-nums text-slate-500">{pct}</span>
    </div>
  )
}

export function ScoreRing({ value, size = 44 }: { value: number; size?: number }) {
  const pct = Math.max(0, Math.min(1, value))
  const stroke = 3.5
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const tone =
    pct >= 0.5 ? 'text-emerald-400' : pct >= 0.35 ? 'text-sky-400' : 'text-slate-600'
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="stroke-slate-800"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - pct)}
          className={`${tone} stroke-current transition-all`}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold tabular-nums text-slate-200">
        {Math.round(pct * 100)}
      </span>
    </div>
  )
}

export function Sparkline({
  values,
  className = '',
}: {
  values: number[]
  className?: string
}) {
  if (values.length === 0) return null
  const width = 88
  const height = 24
  const max = Math.max(...values, 1)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : width
  const points = values
    .map((v, i) => `${i * step},${height - ((v - min) / span) * height}`)
    .join(' ')
  return (
    <svg width={width} height={height} className={className}>
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        className="stroke-current"
      />
    </svg>
  )
}

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string
  value: ReactNode
  hint?: string
}) {
  return (
    <Panel className="p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-100">
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-slate-600">{hint}</div>}
    </Panel>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-800 p-10 text-center text-sm text-slate-500">
      {children}
    </div>
  )
}
