import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { IdeaDetail } from './components/IdeaDetail'
import type { IdeaOut, StatsOut, TopicOut, TrendOut } from './types'
import { MonitorView } from './views/MonitorView'
import { RadarView } from './views/RadarView'
import { TopicsView } from './views/TopicsView'
import { TrendsView } from './views/TrendsView'

type View = 'radar' | 'topics' | 'trends' | 'monitor'
type BackendStatus = 'loading' | 'ok' | 'error'

const VIEWS: [View, string][] = [
  ['radar', 'Radar'],
  ['topics', 'Topic'],
  ['trends', 'Trend'],
  ['monitor', 'Monitor'],
]

function App() {
  const [view, setView] = useState<View>('radar')
  const [backend, setBackend] = useState<BackendStatus>('loading')
  const [ideas, setIdeas] = useState<IdeaOut[]>([])
  const [topics, setTopics] = useState<TopicOut[]>([])
  const [trends, setTrends] = useState<TrendOut[]>([])
  const [stats, setStats] = useState<StatsOut | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const loadAll = useCallback(async () => {
    const [i, t, tr, s] = await Promise.all([
      api.ideas(),
      api.topics(),
      api.trends(),
      api.stats(),
    ])
    setIdeas(i)
    setTopics(t)
    setTrends(tr)
    setStats(s)
  }, [])

  useEffect(() => {
    api
      .health()
      .then((d) => setBackend(d.status === 'ok' ? 'ok' : 'error'))
      .catch(() => setBackend('error'))
    loadAll()
      .catch(() => setError('Impossibile caricare i dati dal backend.'))
      .finally(() => setLoading(false))
  }, [loadAll])

  const running = stats?.last_run?.status === 'running'

  // Mentre un run gira, aggiorna tutto ogni 2s: è il "live" del monitor.
  useEffect(() => {
    if (!running) {
      if (pollRef.current) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }
    pollRef.current = window.setInterval(() => {
      loadAll().catch(() => undefined)
    }, 2000)
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [running, loadAll])

  const startRun = useCallback(async () => {
    setError(null)
    try {
      const res = await api.startRun()
      if (!res.started) setError(res.detail)
      await loadAll()
      setView('monitor')
    } catch {
      setError('Impossibile avviare il run. Il backend è attivo?')
    }
  }, [loadAll])

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-5xl px-4 py-8">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Idea Radar</h1>
            <p className="mt-1 text-sm text-slate-500">
              Segnali da Hacker News, GitHub e riviste, raggruppati e ordinati per
              opportunità.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span
              className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-[11px] font-medium ${
                backend === 'ok'
                  ? 'bg-emerald-500/10 text-emerald-300'
                  : backend === 'error'
                    ? 'bg-rose-500/10 text-rose-300'
                    : 'bg-amber-500/10 text-amber-300'
              }`}
            >
              <span className="size-1.5 rounded-full bg-current" />
              {backend === 'ok'
                ? 'Backend online'
                : backend === 'error'
                  ? 'Backend offline'
                  : 'Verifica…'}
            </span>
            <button
              onClick={startRun}
              disabled={running}
              className="rounded-lg bg-sky-500 px-3.5 py-1.5 text-sm font-medium text-white transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
            >
              {running ? 'Run in corso…' : 'Lancia un run'}
            </button>
          </div>
        </header>

        <nav className="mt-6 flex gap-1 border-b border-slate-800">
          {VIEWS.map(([value, label]) => (
            <button
              key={value}
              onClick={() => setView(value)}
              className={`-mb-px border-b-2 px-3.5 py-2 text-sm font-medium transition ${
                view === value
                  ? 'border-sky-500 text-slate-100'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              {label}
              {value === 'radar' && ideas.length > 0 && (
                <span className="ml-1.5 text-xs text-slate-600">{ideas.length}</span>
              )}
              {value === 'topics' && topics.length > 0 && (
                <span className="ml-1.5 text-xs text-slate-600">{topics.length}</span>
              )}
            </button>
          ))}
        </nav>

        {error && (
          <p className="mt-4 rounded-lg border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}

        <main className="mt-6">
          {view === 'radar' && (
            <RadarView ideas={ideas} loading={loading} onSelect={setSelected} />
          )}
          {view === 'topics' && (
            <TopicsView topics={topics} ideas={ideas} onSelect={setSelected} />
          )}
          {view === 'trends' && <TrendsView trends={trends} />}
          {view === 'monitor' && <MonitorView stats={stats} />}
        </main>
      </div>

      {selected !== null && (
        <IdeaDetail ideaId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

export default App
