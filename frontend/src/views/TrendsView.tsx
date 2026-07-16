import { EmptyState, Panel, Sparkline } from '../components/ui'
import type { TrendOut } from '../types'

function DeltaPill({ value }: { value: number }) {
  if (value === 0)
    return <span className="text-xs tabular-nums text-slate-600">stabile</span>
  const up = value > 0
  return (
    <span
      className={`text-xs font-medium tabular-nums ${
        up ? 'text-emerald-400' : 'text-rose-400'
      }`}
    >
      {up ? '↑' : '↓'} {up ? '+' : ''}
      {value} idee
    </span>
  )
}

export function TrendsView({ trends }: { trends: TrendOut[] }) {
  const hasHistory = trends.some((t) => t.points.length > 1)

  if (trends.length === 0) {
    return (
      <EmptyState>
        Nessun trend ancora. I trend si misurano tra un run e l'altro: servono almeno
        due run.
      </EmptyState>
    )
  }

  return (
    <div className="space-y-4">
      {!hasHistory && (
        <Panel className="border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-200/80">
          C'è un solo run in archivio, quindi le variazioni sono tutte a zero per
          costruzione. Lancia un altro run (anche fra qualche ora) e questa vista
          inizierà a mostrare cosa sale e cosa scende.
        </Panel>
      )}
      <div className="grid gap-3">
        {trends.map((trend) => (
          <Panel key={trend.topic_id} className="flex items-center gap-4 p-4">
            <div className="min-w-0 flex-1">
              <h3 className="truncate font-medium text-slate-100">{trend.label}</h3>
              <div className="mt-1 flex items-center gap-3">
                <DeltaPill value={trend.delta_ideas} />
                <span className="text-xs text-slate-600">
                  {trend.n_ideas} idee · composite{' '}
                  {Math.round(trend.avg_composite * 100)}
                </span>
              </div>
            </div>
            <div
              className={
                trend.delta_ideas > 0
                  ? 'text-emerald-400'
                  : trend.delta_ideas < 0
                    ? 'text-rose-400'
                    : 'text-slate-600'
              }
            >
              <Sparkline values={trend.points.map((p) => p.n_ideas)} />
            </div>
          </Panel>
        ))}
      </div>
    </div>
  )
}
