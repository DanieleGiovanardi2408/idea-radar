import { AnimatedNumber } from '../components/motion'
import { EmptyState, ErrorNotice, Panel, StatCard } from '../components/ui'
import { useStats } from '../hooks/useRadarData'
import type { RunOut, StatsOut } from '../types'

const SOURCE_LABELS: Record<string, string> = {
  hn: 'Hacker News',
  github: 'GitHub',
  rss: 'Riviste e forum',
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('it-IT', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Funnel({ stats }: { stats: StatsOut }) {
  const steps = [
    { label: 'Segnali raccolti', value: stats.n_items },
    { label: 'Idee dopo il clustering', value: stats.n_ideas },
    { label: 'Sopra soglia', value: stats.n_proposed },
  ]
  const max = Math.max(...steps.map((s) => s.value), 1)
  return (
    <Panel className="p-5">
      <h3 className="hud text-slate-500">Imbuto di ingestione</h3>
      <div className="mt-4 space-y-3">
        {steps.map((step, index) => (
          <div
            key={step.label}
            className="flex items-center gap-3"
            title={`${step.label}: ${step.value}`}
          >
            <span className="w-44 shrink-0 text-xs text-slate-400">{step.label}</span>
            <div className="h-6 flex-1 overflow-hidden rounded-md bg-white/[0.04]">
              <div
                className="grow-bar flex h-full items-center justify-end rounded-md bg-gradient-to-r from-signal/50 to-phosphor/80 px-2"
                style={{
                  width: `${Math.max((step.value / max) * 100, 7)}%`,
                  animationDelay: `${index * 140}ms`,
                }}
              >
                <span className="font-display text-xs font-semibold tabular-nums text-abyss">
                  {step.value}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs leading-relaxed text-slate-600">
        Il salto tra segnali e idee è il lavoro del clustering: quanti duplicati
        sono stati fusi.
      </p>
    </Panel>
  )
}

function RunProgress({ run }: { run: RunOut }) {
  const running = run.status === 'running'
  const failed = run.status === 'failed'
  const border = failed
    ? 'border-flare/30'
    : running
      ? 'border-phosphor/30'
      : ''
  return (
    <Panel className={`view-enter relative overflow-hidden p-5 ${border}`}>
      {running && (
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(500px_160px_at_15%_0%,rgba(46,232,162,0.08),transparent)]" />
      )}
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h3 className="hud text-slate-500">
            Run #{run.id} · {formatTime(run.started_at)}
          </h3>
          <p className="mt-1.5 flex items-center gap-2.5 font-display text-lg font-medium tracking-tight text-slate-100">
            {running && (
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-phosphor opacity-70" />
                <span className="relative inline-flex size-2 rounded-full bg-phosphor" />
              </span>
            )}
            {run.phase}
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ${
            run.status === 'done'
              ? 'bg-phosphor/10 text-phosphor ring-phosphor/30'
              : failed
                ? 'bg-flare/10 text-flare ring-flare/30'
                : 'bg-signal/10 text-signal ring-signal/30'
          }`}
        >
          {run.status}
        </span>
      </div>

      {running && (
        <div className="progress-sheen mt-4 h-1.5 rounded-full bg-white/[0.05]" />
      )}

      {run.error && <p className="mt-3 text-xs text-flare">{run.error}</p>}

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(
          [
            ['Raccolti', run.n_items_fetched],
            ['Nuovi', run.n_items_new],
            ['Sopra soglia', run.n_ideas_proposed],
            ['Topic', run.n_topics],
          ] as [string, number][]
        ).map(([label, value]) => (
          <div key={label} className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/[0.05]">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="font-display text-xl font-semibold tabular-nums text-slate-100">
              <AnimatedNumber value={value} />
            </div>
          </div>
        ))}
      </div>

      {run.sources && Object.keys(run.sources).length > 0 && (
        <div className="mt-4">
          <h4 className="hud text-slate-600">Fonti in questo run</h4>
          <ul className="mt-2 space-y-1.5">
            {Object.entries(run.sources).map(([name, s]) => (
              <li
                key={name}
                className="flex items-center justify-between rounded-xl bg-white/[0.03] px-3 py-2 text-xs ring-1 ring-white/[0.05]"
              >
                <span className="text-slate-300">{name.replace('Source', '')}</span>
                {s.error ? (
                  <span className="text-flare">errore: {s.error.slice(0, 40)}</span>
                ) : (
                  <span className="tabular-nums text-slate-500">
                    {s.fetched} raccolti ·{' '}
                    <span className={s.new > 0 ? 'text-phosphor/80' : ''}>
                      {s.new} nuovi
                    </span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  )
}

export function MonitorView() {
  const { data: stats, isError } = useStats()

  if (isError) {
    return <ErrorNotice>Impossibile caricare le statistiche dal backend.</ErrorNotice>
  }
  if (!stats) return <EmptyState>Caricamento delle statistiche…</EmptyState>

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Segnali in DB" value={stats.n_items} />
        <StatCard
          label="Idee"
          value={stats.n_ideas}
          hint={`${stats.n_proposed} sopra soglia · ${stats.n_archived} in archivio`}
          delayMs={60}
        />
        <StatCard label="Topic" value={stats.n_topics} delayMs={120} />
        <StatCard label="Run eseguiti" value={stats.n_runs} delayMs={180} />
      </div>

      {stats.last_run && <RunProgress run={stats.last_run} />}

      <div className="grid gap-3 lg:grid-cols-2">
        <Funnel stats={stats} />

        <Panel className="p-5">
          <h3 className="hud text-slate-500">Fonti attive</h3>
          <ul className="mt-4 space-y-2.5">
            {Object.entries(stats.items_by_source).map(([source, count]) => (
              <li key={source} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2.5 text-slate-300">
                  <span className="relative flex size-1.5">
                    <span
                      className="absolute inline-flex size-full rounded-full bg-phosphor opacity-60"
                      style={{ animation: 'pulse-soft 2.6s ease-in-out infinite' }}
                    />
                    <span className="relative inline-flex size-1.5 rounded-full bg-phosphor" />
                  </span>
                  {SOURCE_LABELS[source] ?? source}
                </span>
                <span className="font-display tabular-nums text-slate-500">
                  {count} segnali
                </span>
              </li>
            ))}
            {Object.keys(stats.items_by_source).length === 0 && (
              <li className="text-sm text-slate-600">
                Nessuna fonte ha ancora prodotto dati.
              </li>
            )}
          </ul>
        </Panel>
      </div>

      {stats.recent_runs.length > 1 && (
        <Panel className="p-5">
          <h3 className="hud text-slate-500">Run recenti</h3>
          <ul className="mt-3 divide-y divide-white/[0.05]">
            {[...stats.recent_runs].reverse().map((run) => (
              <li key={run.id} className="flex items-center justify-between py-2.5 text-xs">
                <span className="text-slate-400">
                  <span className="font-display font-medium text-slate-300">#{run.id}</span> ·{' '}
                  {formatTime(run.started_at)}
                </span>
                <span className="flex items-center gap-3 tabular-nums text-slate-600">
                  {run.n_items} segnali · {run.n_ideas_proposed} sopra soglia
                  <span
                    className={`size-1.5 rounded-full ${
                      run.status === 'done'
                        ? 'bg-phosphor/70'
                        : run.status === 'failed'
                          ? 'bg-flare/80'
                          : 'bg-signal/80'
                    }`}
                    title={run.status}
                  />
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}
