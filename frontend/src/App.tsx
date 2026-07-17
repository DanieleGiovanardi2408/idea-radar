import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
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

/* Il marchio: un mini-radar vivo, la stessa spazzata del quadrante. */
function RadarMark() {
  return (
    <svg viewBox="0 0 44 44" className="size-9 text-phosphor">
      <circle cx="22" cy="22" r="20" fill="none" stroke="currentColor" strokeOpacity="0.25" />
      <circle cx="22" cy="22" r="12.5" fill="none" stroke="currentColor" strokeOpacity="0.18" />
      <circle cx="22" cy="22" r="5.5" fill="none" stroke="currentColor" strokeOpacity="0.14" />
      <g style={{ transformOrigin: '22px 22px', animation: 'sweep-rotate 5s linear infinite' }}>
        <path d="M22 22 L22 2 A20 20 0 0 1 36 8 Z" fill="currentColor" opacity="0.18" />
        <line
          x1="22"
          y1="22"
          x2="22"
          y2="2"
          stroke="currentColor"
          strokeWidth="1.6"
          style={{ filter: 'drop-shadow(0 0 3px rgba(46,232,162,0.9))' }}
        />
      </g>
      <circle cx="22" cy="22" r="2" fill="currentColor" />
      <circle cx="31" cy="13" r="1.8" fill="currentColor" opacity="0.9" />
    </svg>
  )
}

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

  /* Indicatore scorrevole della navigazione */
  const navRef = useRef<HTMLElement | null>(null)
  const tabRefs = useRef<Partial<Record<View, HTMLButtonElement | null>>>({})
  const [indicator, setIndicator] = useState({ left: 0, width: 0 })

  useLayoutEffect(() => {
    const measure = () => {
      const tab = tabRefs.current[view]
      const nav = navRef.current
      if (!tab || !nav) return
      const tabBox = tab.getBoundingClientRect()
      const navBox = nav.getBoundingClientRect()
      setIndicator({ left: tabBox.left - navBox.left, width: tabBox.width })
    }
    measure()
    window.addEventListener('resize', measure)
    // il font display arriva async: rimisura quando è pronto
    document.fonts?.ready.then(measure).catch(() => undefined)
    return () => window.removeEventListener('resize', measure)
  }, [view])

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

  const counts: Partial<Record<View, number>> = {
    radar: ideas.length,
    topics: topics.length,
  }

  return (
    <div className="stage relative min-h-screen text-slate-100">
      {/* scenografia fissa */}
      <div className="vignette pointer-events-none fixed inset-0" />
      <div className="scanline pointer-events-none fixed inset-x-0 top-0 h-40" />

      <div className="relative mx-auto max-w-5xl px-4 py-8">
        <header className="view-enter flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <RadarMark />
            <div>
              <h1 className="font-display text-[26px] font-semibold tracking-tight text-slate-50">
                Idea <span className="text-phosphor">Radar</span>
              </h1>
              <p className="mt-0.5 text-sm text-slate-500">
                Segnali da Hacker News, GitHub e riviste — ciò che sale e non è
                ancora saturo.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span
              className={`glass inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[11px] font-medium ${
                backend === 'ok'
                  ? 'text-phosphor'
                  : backend === 'error'
                    ? 'text-flare'
                    : 'text-ember'
              }`}
            >
              <span className="relative flex size-1.5">
                {backend === 'ok' && (
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
                )}
                <span className="relative inline-flex size-1.5 rounded-full bg-current" />
              </span>
              {backend === 'ok'
                ? 'In ascolto'
                : backend === 'error'
                  ? 'Backend offline'
                  : 'Verifica…'}
            </span>
            <button
              onClick={startRun}
              disabled={running}
              className="group relative overflow-hidden rounded-xl bg-gradient-to-r from-phosphor to-signal px-4 py-2 font-display text-sm font-semibold tracking-tight text-abyss shadow-[0_0_24px_-6px_rgba(46,232,162,0.7)] transition-all duration-300 hover:shadow-[0_0_36px_-6px_rgba(46,232,162,0.9)] hover:brightness-110 disabled:cursor-not-allowed disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-500 disabled:shadow-none"
            >
              <span className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/40 to-transparent transition-transform duration-700 group-hover:translate-x-full" />
              {running ? 'Run in corso…' : 'Lancia un run'}
            </button>
          </div>
        </header>

        <nav
          ref={navRef}
          className="glass view-enter relative mt-6 flex gap-1 rounded-2xl p-1.5"
        >
          {/* indicatore scorrevole */}
          <span
            className="absolute top-1.5 bottom-1.5 rounded-xl bg-phosphor/12 shadow-[inset_0_0_0_1px_rgba(46,232,162,0.28),0_0_18px_-4px_rgba(46,232,162,0.35)] transition-all duration-300 ease-out"
            style={{ left: indicator.left, width: indicator.width }}
          />
          {VIEWS.map(([value, label]) => (
            <button
              key={value}
              ref={(el) => {
                tabRefs.current[value] = el
              }}
              onClick={() => setView(value)}
              className={`relative z-10 flex-1 rounded-xl px-3.5 py-2 font-display text-sm font-medium tracking-tight transition-colors duration-300 sm:flex-none ${
                view === value ? 'text-phosphor' : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {label}
              {counts[value] !== undefined && counts[value]! > 0 && (
                <span
                  className={`ml-1.5 text-xs tabular-nums ${
                    view === value ? 'text-phosphor/60' : 'text-slate-600'
                  }`}
                >
                  {counts[value]}
                </span>
              )}
            </button>
          ))}
        </nav>

        {error && (
          <p className="view-enter mt-4 rounded-xl border border-flare/25 bg-flare/5 px-3.5 py-2.5 text-sm text-flare">
            {error}
          </p>
        )}

        <main className="mt-6">
          <div key={view} className="view-enter">
            {view === 'radar' && (
              <RadarView ideas={ideas} loading={loading} onSelect={setSelected} />
            )}
            {view === 'topics' && (
              <TopicsView topics={topics} ideas={ideas} onSelect={setSelected} />
            )}
            {view === 'trends' && <TrendsView trends={trends} />}
            {view === 'monitor' && <MonitorView stats={stats} />}
          </div>
        </main>

        <footer className="hud mt-12 flex items-center justify-between pb-4 text-slate-700">
          <span>idea radar · dati e modelli in locale</span>
          <span>{stats ? `${stats.n_runs} scansioni eseguite` : '…'}</span>
        </footer>
      </div>

      {selected !== null && (
        <IdeaDetail ideaId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

export default App
