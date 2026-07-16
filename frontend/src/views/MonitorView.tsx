import { EmptyState, Panel, StatCard } from '../components/ui'
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
      <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
        Imbuto di ingestione
      </h3>
      <div className="mt-4 space-y-3">
        {steps.map((step) => (
          <div key={step.label} className="flex items-center gap-3">
            <span className="w-44 shrink-0 text-xs text-slate-500">{step.label}</span>
            <div className="h-6 flex-1 overflow-hidden rounded-md bg-slate-800/50">
              <div
                className="flex h-full items-center justify-end rounded-md bg-gradient-to-r from-sky-600/70 to-cyan-500/70 px-2 text-xs font-medium tabular-nums text-white transition-all"
                style={{ width: `${Math.max((step.value / max) * 100, 6)}%` }}
              >
                {step.value}
              </div>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-slate-600">
        Il salto tra segnali e idee è il lavoro del clustering: quanti duplicati sono
        stati fusi.
      </p>
    </Panel>
  )
}

function RunProgress({ run }: { run: RunOut }) {
  const running = run.status === 'running'
  const tone =
    run.status === 'failed'
      ? 'border-rose-500/30 bg-rose-500/5'
      : running
        ? 'border-sky-500/30 bg-sky-500/5'
        : 'border-slate-800/80 bg-slate-900/40'
  return (
    <Panel className={`p-5 ${tone}`}>
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Run #{run.id} · {formatTime(run.started_at)}
          </h3>
          <p className="mt-1 flex items-center gap-2 text-sm text-slate-200">
            {running && (
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-sky-400 opacity-75" />
                <span className="relative inline-flex size-2 rounded-full bg-sky-500" />
              </span>
            )}
            {run.phase}
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ${
            run.status === 'done'
              ? 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30'
              : run.status === 'failed'
                ? 'bg-rose-500/10 text-rose-300 ring-rose-500/30'
                : 'bg-sky-500/10 text-sky-300 ring-sky-500/30'
          }`}
        >
          {run.status}
        </span>
      </div>

      {run.error && <p className="mt-3 text-xs text-rose-300">{run.error}</p>}

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ['Raccolti', run.n_items_fetched],
          ['Nuovi', run.n_items_new],
          ['Sopra soglia', run.n_ideas_proposed],
          ['Topic', run.n_topics],
        ].map(([label, value]) => (
          <div key={label as string} className="rounded-lg bg-slate-900/60 p-2.5">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="text-lg font-semibold tabular-nums text-slate-100">
              {value}
            </div>
          </div>
        ))}
      </div>

      {run.sources && Object.keys(run.sources).length > 0 && (
        <div className="mt-4">
          <h4 className="text-[11px] font-medium uppercase tracking-wide text-slate-600">
            Fonti in questo run
          </h4>
          <ul className="mt-2 space-y-1.5">
            {Object.entries(run.sources).map(([name, s]) => (
              <li
                key={name}
                className="flex items-center justify-between rounded-lg bg-slate-900/60 px-3 py-1.5 text-xs"
              >
                <span className="text-slate-400">{name.replace('Source', '')}</span>
                {s.error ? (
                  <span className="text-rose-400">errore: {s.error.slice(0, 40)}</span>
                ) : (
                  <span className="tabular-nums text-slate-500">
                    {s.fetched} raccolti · {s.new} nuovi
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

export function MonitorView({ stats }: { stats: StatsOut | null }) {
  if (!stats) return <EmptyState>Caricamento delle statistiche…</EmptyState>

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Segnali in DB" value={stats.n_items} />
        <StatCard label="Idee" value={stats.n_ideas} hint={`${stats.n_proposed} sopra soglia`} />
        <StatCard label="Topic" value={stats.n_topics} />
        <StatCard label="Run eseguiti" value={stats.n_runs} />
      </div>

      {stats.last_run && <RunProgress run={stats.last_run} />}

      <div className="grid gap-3 lg:grid-cols-2">
        <Funnel stats={stats} />

        <Panel className="p-5">
          <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Fonti attive
          </h3>
          <ul className="mt-4 space-y-2">
            {Object.entries(stats.items_by_source).map(([source, count]) => (
              <li key={source} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2 text-slate-300">
                  <span className="size-1.5 rounded-full bg-emerald-400" />
                  {SOURCE_LABELS[source] ?? source}
                </span>
                <span className="tabular-nums text-slate-500">{count} segnali</span>
              </li>
            ))}
            {Object.keys(stats.items_by_source).length === 0 && (
              <li className="text-sm text-slate-600">Nessuna fonte ha ancora prodotto dati.</li>
            )}
          </ul>
        </Panel>
      </div>

      {stats.recent_runs.length > 1 && (
        <Panel className="p-5">
          <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Run recenti
          </h3>
          <ul className="mt-3 divide-y divide-slate-800/80">
            {[...stats.recent_runs].reverse().map((run) => (
              <li
                key={run.id}
                className="flex items-center justify-between py-2 text-xs"
              >
                <span className="text-slate-400">
                  #{run.id} · {formatTime(run.started_at)}
                </span>
                <span className="tabular-nums text-slate-600">
                  {run.n_items} segnali · {run.n_ideas_proposed} sopra soglia ·{' '}
                  {run.status}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}
