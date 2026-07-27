import { useLayoutEffect, useRef, useState } from 'react'
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useSearchParams,
} from 'react-router-dom'
import { IdeaDetail } from './components/IdeaDetail'
import {
  useHealth,
  useIdeas,
  useRunWatcher,
  useStartRun,
  useStats,
  useTopics,
} from './hooks/useRadarData'
import { SignalRhythm } from './components/SignalRhythm'
import { TrendingVideos } from './components/TrendingVideos'
import { MonitorView } from './views/MonitorView'
import { RadarView } from './views/RadarView'
import { TopicsView } from './views/TopicsView'
import { TrendsView } from './views/TrendsView'

const TABS: [string, string][] = [
  ['/radar', 'Radar'],
  ['/topics', 'Topic'],
  ['/trends', 'Trend'],
  ['/monitor', 'Monitor'],
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
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  // La cache di React Query fa da stato condiviso: queste stesse query
  // alimentano anche le viste, senza fetch duplicate.
  const health = useHealth()
  const { data: stats } = useStats()
  const { data: ideas } = useIdeas()
  // Stessi parametri di default della vista Topic, così il contatore nella nav
  // dice lo stesso numero della lista (e riusa la cache invece di rifetchare).
  const { data: topics } = useTopics({ minIdeas: 2, orderBy: 'n_ideas' })
  // Montato una volta sola: al passaggio running → done invalida le risorse.
  const running = useRunWatcher()

  const startRun = useStartRun()
  const [runError, setRunError] = useState<string | null>(null)

  const backend =
    health.isPending ? 'loading' : health.data?.status === 'ok' ? 'ok' : 'error'
  const onRadar = pathname === '/radar' || pathname === '/'

  /* Drawer deep-linkabile: ?idea=<id> vive sopra qualunque vista. L'apertura
     è una nuova entry di history, quindi il back del browser lo chiude. */
  const ideaParam = searchParams.get('idea')
  const parsedIdea = ideaParam !== null ? Number(ideaParam) : NaN
  const selected = Number.isInteger(parsedIdea) ? parsedIdea : null

  const openIdea = (id: number) => {
    const next = new URLSearchParams(searchParams)
    next.set('idea', String(id))
    setSearchParams(next)
  }
  const closeIdea = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('idea')
    // replace: chiudere non deve aggiungere un'altra entry alla history
    setSearchParams(next, { replace: true })
  }

  /* Indicatore scorrevole della navigazione */
  const navRef = useRef<HTMLElement | null>(null)
  const tabRefs = useRef<Record<string, HTMLAnchorElement | null>>({})
  const [indicator, setIndicator] = useState({ left: 0, width: 0 })

  const counts: Record<string, number | undefined> = {
    '/radar': ideas?.length,
    '/topics': topics?.length,
  }

  useLayoutEffect(() => {
    const measure = () => {
      const tab = tabRefs.current[pathname]
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
    // i contatori arrivano async e cambiano la larghezza dei tab
  }, [pathname, ideas?.length, topics?.length])

  const onStartRun = () => {
    setRunError(null)
    startRun.mutate(undefined, {
      onSuccess: (res) => {
        if (res.started) navigate('/monitor')
        else setRunError(res.detail)
      },
      onError: () =>
        setRunError('Impossibile avviare il run. Il backend è attivo?'),
    })
  }

  return (
    <div className="stage relative min-h-screen text-slate-100">
      {/* scenografia fissa */}
      <div className="vignette pointer-events-none fixed inset-0" />
      <div className="scanline pointer-events-none fixed inset-x-0 top-0 h-40" />

      {/* Il Radar è una sala controllo a tre colonne e ha bisogno di respiro;
          le altre viste restano nella colonna leggibile di sempre. */}
      <div
        className={`relative mx-auto px-4 py-8 ${
          onRadar ? 'max-w-[92rem]' : 'max-w-5xl'
        }`}
      >
        <header className="view-enter flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <RadarMark />
            <div>
              <h1 className="font-display text-[26px] font-semibold tracking-tight text-slate-50">
                Idea <span className="text-phosphor">Radar</span>
              </h1>
              <p className="mt-0.5 text-sm text-slate-500">
                Segnali da Hacker News, GitHub, Hugging Face, Stack Exchange,
                npm, arXiv e 20 feed — ciò che sale e non è ancora saturo.
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
              onClick={onStartRun}
              disabled={running || startRun.isPending}
              className="group relative overflow-hidden rounded-xl bg-gradient-to-r from-phosphor to-signal px-4 py-2 font-display text-sm font-semibold tracking-tight text-abyss shadow-[0_0_24px_-6px_rgba(46,232,162,0.7)] transition-all duration-300 hover:shadow-[0_0_36px_-6px_rgba(46,232,162,0.9)] hover:brightness-110 disabled:cursor-not-allowed disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-500 disabled:shadow-none"
            >
              <span className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/40 to-transparent transition-transform duration-700 group-hover:translate-x-full" />
              {running ? 'Run in corso…' : 'Lancia un run'}
            </button>
          </div>
        </header>

        {/* Non `role="tablist"`: i tab sono link con un URL e la loro vista è
            una pagina, non un pannello che vive qui dentro. Marcarli come tab
            farebbe perdere a chi usa uno screen reader l'informazione che sono
            link (e imporrebbe la navigazione a frecce di un widget composito).
            `<nav>` con un nome e l'`aria-current` che NavLink già emette è il
            pattern corretto da quando il routing è passato agli URL. */}
        <nav
          ref={navRef}
          aria-label="Viste del radar"
          className="glass view-enter relative mt-6 flex gap-1 rounded-2xl p-1.5"
        >
          {/* indicatore scorrevole */}
          <span
            className="absolute top-1.5 bottom-1.5 rounded-xl bg-phosphor/12 shadow-[inset_0_0_0_1px_rgba(46,232,162,0.28),0_0_18px_-4px_rgba(46,232,162,0.35)] transition-all duration-300 ease-out"
            style={{ left: indicator.left, width: indicator.width }}
          />
          {TABS.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              ref={(el) => {
                tabRefs.current[to] = el
              }}
              className={({ isActive }) =>
                `relative z-10 flex-1 rounded-xl px-3.5 py-2 text-center font-display text-sm font-medium tracking-tight transition-colors duration-300 sm:flex-none ${
                  isActive ? 'text-phosphor' : 'text-slate-500 hover:text-slate-300'
                }`
              }
            >
              {label}
              {counts[to] !== undefined && counts[to]! > 0 && (
                <span
                  className={`ml-1.5 text-xs tabular-nums ${
                    pathname === to ? 'text-phosphor/60' : 'text-slate-600'
                  }`}
                >
                  {counts[to]}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        {runError && (
          <p className="view-enter mt-4 rounded-xl border border-flare/25 bg-flare/5 px-3.5 py-2.5 text-sm text-flare">
            {runError}
          </p>
        )}

        <main className="mt-6">
          <div key={pathname} className="view-enter">
            {/* La sala controllo esiste solo sul Radar: le altre viste sono
                liste, e stringerle in una colonna centrale le peggiora. */}
            {/* La radice si normalizza su /radar prima di disegnare qualsiasi
                cosa. Senza questo passaggio `onRadar` era vero anche su "/", ma
                nessuna route interna corrispondeva: chi apriva l'indirizzo nudo
                trovava la sala controllo con la colonna centrale vuota, il tab
                non evidenziato e l'indicatore a larghezza zero (i tab sono
                indicizzati per pathname, e "/" non è tra loro). */}
            {pathname === '/' ? (
              <Navigate to="/radar" replace />
            ) : onRadar ? (
              /* Le colonne laterali sono larghe perché il loro contenuto lo
                 richiede: una heatmap di 7×24 celle e un player video a 16:9
                 sotto i 20rem diventano decorazione illeggibile. */
              <div className="grid gap-4 xl:grid-cols-[21rem_minmax(0,1fr)_21rem] 2xl:grid-cols-[23rem_minmax(0,1fr)_23rem]">
                {/* Su schermi stretti i pannelli vanno SOTTO il quadrante:
                    l'ordine visivo segue l'importanza, non il DOM. */}
                <aside className="order-2 grid gap-4 xl:order-1 xl:sticky xl:top-6 xl:self-start">
                  <TrendingVideos />
                </aside>
                {/* Niente <Routes> qui: siamo in questo ramo proprio perché
                    l'indirizzo è il Radar. Un router annidato da tenere in
                    sincrono con la condizione qui sopra è la trappola che ha
                    svuotato la colonna. */}
                <div className="order-1 min-w-0 xl:order-2">
                  <RadarView onSelect={openIdea} />
                </div>
                <aside className="order-3 grid gap-4 xl:sticky xl:top-6 xl:self-start">
                  <SignalRhythm />
                </aside>
              </div>
            ) : (
            <Routes>
              <Route path="/" element={<Navigate to="/radar" replace />} />
              <Route path="/radar" element={<RadarView onSelect={openIdea} />} />
              <Route path="/topics" element={<TopicsView onSelect={openIdea} />} />
              {/* deep link dal Trend: apre quel tema già espanso */}
              <Route
                path="/topics/:topicId"
                element={<TopicsView onSelect={openIdea} />}
              />
              <Route path="/trends" element={<TrendsView />} />
              <Route path="/monitor" element={<MonitorView />} />
              <Route path="*" element={<Navigate to="/radar" replace />} />
            </Routes>
            )}
          </div>
        </main>

        <footer className="hud mt-12 flex items-center justify-between pb-4 text-slate-700">
          <span>idea radar · dati e modelli in locale</span>
          <span>{stats ? `${stats.n_runs} scansioni eseguite` : '…'}</span>
        </footer>
      </div>

      {selected !== null && (
        <IdeaDetail ideaId={selected} onClose={closeIdea} />
      )}
    </div>
  )
}

export default App
