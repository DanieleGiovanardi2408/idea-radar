import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { staggerDelay } from '../components/motion'
import {
  AreaSpark,
  EmptyState,
  ErrorNotice,
  Panel,
  SkeletonCard,
} from '../components/ui'
import { useTrends } from '../hooks/useRadarData'

function DeltaChip({ value }: { value: number }) {
  if (value === 0) {
    return (
      <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[11px] font-medium text-slate-500 ring-1 ring-white/[0.07]">
        = stabile
      </span>
    )
  }
  const up = value > 0
  return (
    <span
      className={`rounded-full px-2 py-0.5 font-display text-[11px] font-semibold tabular-nums ring-1 ${
        up
          ? 'bg-phosphor/10 text-phosphor ring-phosphor/30'
          : 'bg-flare/10 text-flare ring-flare/30'
      }`}
    >
      {up ? '↑' : '↓'} {up ? '+' : ''}
      {value} idee
    </span>
  )
}

function trendTone(delta: number): 'up' | 'down' | 'flat' {
  if (delta > 0) return 'up'
  if (delta < 0) return 'down'
  return 'flat'
}

function shortDate(value: string): string {
  return new Date(value).toLocaleString('it-IT', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function TrendsView() {
  const { data: trends = [], isPending, isError } = useTrends()
  const hasHistory = trends.some((t) => t.points.length > 1)

  if (isError) {
    return <ErrorNotice>Impossibile caricare i trend dal backend.</ErrorNotice>
  }

  if (isPending) {
    return (
      <div className="grid gap-3">
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }

  if (trends.length === 0) {
    return (
      <EmptyState>
        Nessun trend ancora. I trend si misurano tra un run e l'altro: servono
        almeno due run.
      </EmptyState>
    )
  }

  const mover = trends[0]
  const rising = trends.filter((t) => t.delta_ideas > 0).length

  return (
    <div className="space-y-4">
      {!hasHistory && (
        <Panel className="border-ember/25 p-4 text-sm text-ember/90">
          C'è un solo run in archivio, quindi le variazioni sono tutte a zero per
          costruzione. Con i run schedulati attivi, questa vista si popola da sola.
        </Panel>
      )}

      {hasHistory && mover && mover.delta_ideas > 0 && (
        <Panel className="view-enter relative overflow-hidden p-5">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(420px_180px_at_18%_0%,rgba(46,232,162,0.09),transparent)]" />
          <div className="hud text-phosphor/70">in ascesa adesso</div>
          <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0">
              <Link
                to={`/topics/${mover.topic_id}`}
                className="font-display text-xl font-semibold tracking-tight text-slate-50 outline-none hover:text-phosphor focus-visible:ring-2 focus-visible:ring-phosphor/40"
              >
                {mover.label}
              </Link>
              <div className="mt-2 flex items-center gap-3">
                <DeltaChip value={mover.delta_ideas} />
                <span className="text-xs text-slate-500">
                  {mover.n_ideas} idee · composite medio{' '}
                  {Math.round(mover.avg_composite * 100)}
                </span>
              </div>
            </div>
            <AreaSpark
              values={mover.points.map((p) => p.n_ideas)}
              tone="up"
              width={280}
              height={72}
              format={(v, i) => `${v} idee · ${shortDate(mover.points[i].started_at)}`}
            />
          </div>
        </Panel>
      )}

      <div className="hud px-1 text-slate-600">
        {rising} in salita su {trends.length} topic osservati
      </div>

      <div className="grid gap-3">
        {trends.slice(hasHistory && mover && mover.delta_ideas > 0 ? 1 : 0).map((trend, index) => (
          <Panel
            key={trend.topic_id}
            interactive
            className="stagger flex items-center gap-4 p-4"
          >
            {/* Il pannello era già `interactive` ma non portava da nessuna parte:
                ora è il link al tema, aperto e pronto da leggere. */}
            <Link
              to={`/topics/${trend.topic_id}`}
              aria-label={`Apri il tema ${trend.label}`}
              className="flex min-w-0 flex-1 items-center gap-4 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-phosphor/40"
              style={staggerDelay(index) as CSSProperties}
            >
              <div className="min-w-0 flex-1">
                <h3 className="truncate font-display font-medium tracking-tight text-slate-100">
                  {trend.label}
                </h3>
                <div className="mt-1.5 flex items-center gap-3">
                  <DeltaChip value={trend.delta_ideas} />
                  <span className="text-xs text-slate-600">
                    {trend.n_ideas} idee · composite{' '}
                    {Math.round(trend.avg_composite * 100)}
                  </span>
                </div>
              </div>
              <AreaSpark
                values={trend.points.map((p) => p.n_ideas)}
                tone={trendTone(trend.delta_ideas)}
                format={(v, i) => `${v} idee · ${shortDate(trend.points[i].started_at)}`}
              />
            </Link>
          </Panel>
        ))}
      </div>
    </div>
  )
}
