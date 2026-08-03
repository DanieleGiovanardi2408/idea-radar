import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import {
  useIdea,
  useMarkSeen,
  usePatchIdea,
  useWorkspace,
  useWorkspaceActions,
} from '../hooks/useRadarData'
import type { IdeaDetailOut } from '../types'
import { dayMonthYear } from '../dates'
import {
  AreaSpark,
  Badge,
  DIFFICULTY_STYLES,
  IconArrowUpRight,
  IconDismiss,
  IconPin,
  IconRestore,
  IconX,
  MetricBar,
  ScoreRing,
  STATUS_STYLES,
} from './ui'

/* Larghezza del dossier: trascinabile dal bordo, ricordata tra le sessioni.
   null = default responsive (le classi max-w-*); un numero = scelta esplicita
   dell'utente, che vince finché non fa doppio click sulla maniglia. */
const DRAWER_WIDTH_KEY = 'idea-radar:drawer-width'
const DRAWER_MIN = 420

function clampDrawerWidth(w: number): number {
  // Mai più largo dello schermo meno un margine: il backdrop deve restare
  // cliccabile, altrimenti sparisce il modo più naturale di chiudere.
  const max = Math.max(DRAWER_MIN, Math.min(1100, window.innerWidth - 240))
  return Math.round(Math.min(max, Math.max(DRAWER_MIN, w)))
}

function savedDrawerWidth(): number | null {
  const raw = Number(localStorage.getItem(DRAWER_WIDTH_KEY))
  return Number.isFinite(raw) && raw >= DRAWER_MIN ? raw : null
}

const METRIC_HINTS: Record<string, string> = {
  Heat: 'Velocità di crescita (stelle/giorno, engagement), non popolarità assoluta.',
  Credibility: 'Affidabilità della fonte e presenza di un autore identificabile.',
  Feasibility: 'Quanto è realizzabile da un team di 1-3 persone (stima LLM).',
  Opportunity: 'Recente E non ancora saturo: un mercato già chiuso vale poco.',
  Fit: 'Aderenza alle tue keyword. Moltiplica il punteggio: fuori tema = abbattuta.',
}

const METRICS: { label: string; key: keyof IdeaDetailOut }[] = [
  { label: 'Heat', key: 'heat' },
  { label: 'Credibility', key: 'credibility' },
  { label: 'Feasibility', key: 'feasibility' },
  { label: 'Opportunity', key: 'opportunity' },
  { label: 'Fit', key: 'fit' },
]

/** Sezione con etichetta HUD e ingresso scaglionato. */
function Section({
  label,
  delayMs = 0,
  children,
}: {
  label: string
  delayMs?: number
  children: ReactNode
}) {
  return (
    <section className="stagger" style={{ animationDelay: `${delayMs}ms` }}>
      <h3 className="hud text-slate-500">{label}</h3>
      <div className="mt-2.5">{children}</div>
    </section>
  )
}

export function IdeaDetail({
  ideaId,
  onClose,
}: {
  ideaId: number
  onClose: () => void
}) {
  const { data: idea, isError } = useIdea(ideaId)
  // Una mutation per concetto, non una condivisa: così il pin non disabilita la
  // nota, e nessuna delle due viene bloccata dal "visto" automatico.
  const { mutate: patchIdea } = usePatchIdea()
  const noteMutation = usePatchIdea()
  const { mutate: markSeen } = useMarkSeen()

  // Sviluppo: sapere se QUESTA idea è già sul tavolo, e portarcela/toglierla.
  const { data: workspaceEntries } = useWorkspace()
  const workspaceActions = useWorkspaceActions()
  const inWorkspace =
    workspaceEntries?.some((e) => e.idea_id === ideaId) ?? false

  // Alla prima apertura del dettaglio il segnale è "visto": una sola PATCH
  // per apertura, il ref evita i reinvii ad ogni render.
  const seenFor = useRef<number | null>(null)
  useEffect(() => {
    if (seenFor.current === ideaId) return
    seenFor.current = ideaId
    markSeen(ideaId)
    // markSeen (mutate di React Query) è stabile tra i render
  }, [ideaId, markSeen])

  // La nota è testo locale finché non salvi: seed una volta per idea, così
  // un refetch in background non ti cancella quello che stai scrivendo.
  const [note, setNote] = useState('')
  const seededFor = useRef<number | null>(null)
  useEffect(() => {
    if (idea && seededFor.current !== ideaId) {
      seededFor.current = ideaId
      setNote(idea.note ?? '')
    }
  }, [idea, ideaId])

  // Nessuna guardia su "c'è un'altra azione in corso": scartava il salvataggio
  // in silenzio. L'unico motivo per non chiamare il server è che il testo sia
  // già quello salvato.
  const saveNote = () => {
    if (!idea) return
    const trimmed = note.trim()
    const next = trimmed === '' ? null : trimmed
    if (next === (idea.note ?? null)) return
    noteMutation.mutate({ id: ideaId, body: { note: next } })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // `aria-modal` dice che fuori non c'è nulla: la trappola lo rende vero anche
  // per il Tab, e restituisce il focus alla card da cui si è arrivati.
  const trappola = useRef<HTMLDivElement>(null)
  useFocusTrap(trappola)

  // Ridimensionamento dal bordo: pointer capture sulla maniglia, così il
  // trascinamento continua anche quando il mouse esce dalla maniglia stessa.
  const [width, setWidth] = useState<number | null>(savedDrawerWidth)
  const widthRef = useRef(width)
  widthRef.current = width

  const persistWidth = () => {
    if (widthRef.current === null) localStorage.removeItem(DRAWER_WIDTH_KEY)
    else localStorage.setItem(DRAWER_WIDTH_KEY, String(widthRef.current))
  }

  const startDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const handle = e.currentTarget
    // Optional chaining: jsdom (i test) non implementa la pointer capture.
    handle.setPointerCapture?.(e.pointerId)
    const onMove = (ev: PointerEvent) =>
      setWidth(clampDrawerWidth(window.innerWidth - ev.clientX))
    const onUp = () => {
      handle.removeEventListener('pointermove', onMove)
      handle.removeEventListener('pointerup', onUp)
      persistWidth()
    }
    handle.addEventListener('pointermove', onMove)
    handle.addEventListener('pointerup', onUp)
  }

  const resetWidth = () => {
    setWidth(null)
    localStorage.removeItem(DRAWER_WIDTH_KEY)
  }

  const nudgeWidth = (delta: number) => {
    // Da tastiera si parte dalla larghezza reale, anche se è quella di default.
    // `|| 576`, non `??`: in ambienti senza layout (jsdom) clientWidth è 0.
    const current =
      widthRef.current ??
      (trappola.current?.querySelector('aside')?.clientWidth || 576)
    const next = clampDrawerWidth(current + delta)
    setWidth(next)
    localStorage.setItem(DRAWER_WIDTH_KEY, String(next))
  }

  const history = idea?.history ?? []
  const sparkTone =
    history.length > 1
      ? history[history.length - 1].composite >= history[0].composite
        ? 'up'
        : 'down'
      : 'accent'
  const dismissed = idea ? idea.dismissed_at !== null : false

  return (
    <div
      ref={trappola}
      className="fixed inset-0 z-50 flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-label={idea ? idea.label : 'Dettaglio idea'}
      data-testid="idea-detail"
    >
      {/* Backdrop: il click chiude, come da convenzione dei drawer; la via da
          tastiera è Escape (gestito sopra), non un finto bottone a schermo intero. */}
      {/* oxlint-disable-next-line click-events-have-key-events, no-static-element-interactions */}
      <div
        className="overlay-enter absolute inset-0 bg-abyss/75 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Su schermi larghi il dossier respira: 576px erano pochi per mosse,
          angolo di business, KPI e segnali tutti impilati. Il default è
          responsive; se l'utente ha trascinato il bordo, vince la sua scelta. */}
      <aside
        className={`drawer-enter relative ml-auto flex h-full w-full flex-col overflow-y-auto border-l border-white/10 bg-deep/95 shadow-[-24px_0_60px_-24px_rgba(0,0,0,0.9)] backdrop-blur-xl ${
          width === null ? 'max-w-xl lg:max-w-2xl 2xl:max-w-3xl' : ''
        }`}
        style={width !== null ? { width, maxWidth: '100vw' } : undefined}
      >
        {/* Maniglia di ridimensionamento: trascina per allargare/restringere,
            doppio click per tornare al default, frecce ← → da tastiera. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Ridimensiona il dossier: trascina, frecce ← → da tastiera, doppio click per il default"
          tabIndex={0}
          onPointerDown={startDrag}
          onDoubleClick={resetWidth}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') nudgeWidth(32)
            if (e.key === 'ArrowRight') nudgeWidth(-32)
          }}
          className="absolute inset-y-0 left-0 z-20 w-2 cursor-col-resize touch-none transition-colors hover:bg-phosphor/20 focus-visible:bg-phosphor/25 focus-visible:outline-none"
        />
        {/* accento fosforo sul bordo del quadrante */}
        <span className="pointer-events-none absolute inset-y-0 left-0 w-px bg-gradient-to-b from-transparent via-phosphor/50 to-transparent" />
        <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(420px_180px_at_15%_0%,rgba(46,232,162,0.08),transparent)]" />

        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-white/10 bg-deep/85 p-5 backdrop-blur-xl">
          <div className="min-w-0">
            <span className="hud text-phosphor/70">dossier segnale</span>
            {idea ? (
              <>
                <h2 className="mt-1 font-display text-lg font-semibold tracking-tight text-slate-50">
                  {idea.label}
                </h2>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {idea.pinned && (
                    <Badge className="bg-phosphor/10 text-phosphor ring-phosphor/30">
                      <IconPin filled /> pinnata
                    </Badge>
                  )}
                  {dismissed && (
                    <Badge className="bg-flare/10 text-flare ring-flare/30">
                      scartata
                    </Badge>
                  )}
                  <Badge className={STATUS_STYLES[idea.status] ?? STATUS_STYLES.processed}>
                    {idea.status}
                  </Badge>
                  {idea.difficulty && (
                    <Badge className={DIFFICULTY_STYLES[idea.difficulty]}>
                      difficoltà: {idea.difficulty}
                    </Badge>
                  )}
                  {/* Il tema (profilo) prima del topic: è il macro, dichiarato
                      in config.yaml, mentre il topic è il micro trovato dagli
                      embedding. Assente quando nessun tema reclama l'idea. */}
                  {idea.profile && (
                    <Badge className="bg-signal/10 text-signal ring-signal/25">
                      {idea.profile}
                    </Badge>
                  )}
                  {idea.topic_label && (
                    <Badge className="bg-white/[0.04] text-slate-300 ring-white/10">
                      {idea.topic_label}
                    </Badge>
                  )}
                </div>
              </>
            ) : (
              <h2 className="mt-1 font-display text-lg font-semibold text-slate-500">
                {isError ? 'Segnale non raggiungibile' : 'Sintonizzazione…'}
              </h2>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {idea && (
              <>
                {/* Sviluppa: porta l'idea sul tavolo di lavoro (vista Sviluppo).
                    Le sue mosse diventano una checklist; togliere non cancella
                    l'idea, solo il piano. */}
                <button
                  onClick={() =>
                    inWorkspace
                      ? workspaceActions.remove.mutate(ideaId)
                      : workspaceActions.add.mutate(ideaId)
                  }
                  title={
                    inWorkspace ? 'Togli dal tavolo Sviluppo' : 'Porta in Sviluppo'
                  }
                  aria-label={
                    inWorkspace ? 'Togli dal tavolo Sviluppo' : 'Porta in Sviluppo'
                  }
                  aria-pressed={inWorkspace}
                  className={`glass glass-hover rounded-xl px-2.5 py-2 font-display text-xs font-medium transition-colors ${
                    inWorkspace ? 'text-phosphor' : 'text-slate-400 hover:text-phosphor'
                  }`}
                >
                  {inWorkspace ? 'In sviluppo ✓' : 'Sviluppa'}
                </button>
                <button
                  onClick={() =>
                    patchIdea({ id: ideaId, body: { pinned: !idea.pinned } })
                  }
                  title={idea.pinned ? 'Togli il pin' : 'Pinna in cima'}
                  aria-label={idea.pinned ? 'Togli il pin' : 'Pinna in cima'}
                  className={`glass glass-hover rounded-xl p-2 transition-colors disabled:opacity-50 ${
                    idea.pinned ? 'text-phosphor' : 'text-slate-400 hover:text-phosphor'
                  }`}
                >
                  <IconPin filled={idea.pinned} />
                </button>
                <button
                  onClick={() =>
                    patchIdea({ id: ideaId, body: { dismissed: !dismissed } })
                  }
                  title={dismissed ? 'Ripristina' : 'Scarta'}
                  aria-label={dismissed ? 'Ripristina' : 'Scarta'}
                  className={`glass glass-hover rounded-xl p-2 transition-colors disabled:opacity-50 ${
                    dismissed
                      ? 'text-slate-400 hover:text-phosphor'
                      : 'text-slate-400 hover:text-flare'
                  }`}
                >
                  {dismissed ? <IconRestore /> : <IconDismiss />}
                </button>
              </>
            )}
            <button
              onClick={onClose}
              className="glass glass-hover rounded-xl p-2 text-slate-400 hover:text-phosphor"
              aria-label="Chiudi"
            >
              <IconX />
            </button>
          </div>
        </header>

        {isError && (
          <p className="p-5 text-sm text-flare">Impossibile caricare il dettaglio.</p>
        )}

        {!idea && !isError && (
          <div className="space-y-4 p-5">
            <div className="flex items-center gap-4">
              <div className="size-16 shrink-0 rounded-full bg-white/[0.05]" />
              <div className="flex-1 space-y-2.5">
                <div className="h-3 w-24 rounded bg-white/[0.06]" />
                <div className="h-3 w-4/5 rounded bg-white/[0.04]" />
                <div className="h-3 w-2/3 rounded bg-white/[0.04]" />
              </div>
            </div>
            <div className="progress-sheen h-1 rounded-full bg-white/[0.05]" />
          </div>
        )}

        {idea && (
          <div className="space-y-7 p-5">
            <section
              className="stagger flex items-start gap-4"
              style={{ animationDelay: '0ms' }}
            >
              <ScoreRing value={idea.composite} size={68} />
              <div className="min-w-0 flex-1">
                <h3 className="hud text-slate-500">Perché</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-300">
                  {idea.why_text || 'Nessuna motivazione generata.'}
                </p>
              </div>
            </section>

            {idea.summary && (
              <Section label="Sintesi" delayMs={60}>
                <p className="text-sm leading-relaxed text-slate-400">{idea.summary}</p>
              </Section>
            )}

            {/* Il "cosa fartene": presente solo se il run l'ha già generato
                (idee sopra soglia). Niente placeholder per le altre: una
                sezione vuota su ogni idea sarebbe rumore. */}
            {((idea.moves?.length ?? 0) > 0 || idea.angle) && (
              <Section label="Le mosse" delayMs={90}>
                {(idea.moves?.length ?? 0) > 0 && (
                  <ul className="space-y-2">
                    {idea.moves!.map((move, i) => (
                      <li
                        key={i}
                        className="flex gap-2.5 text-sm leading-relaxed text-slate-300"
                      >
                        <span aria-hidden className="mt-px shrink-0 text-phosphor">
                          ▸
                        </span>
                        <span>{move}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {idea.angle && (
                  /* Richiudibile: è il blocco più lungo del dossier e non
                     serve a ogni apertura — chiuso di default, il titolo
                     resta come invito. <details> nativo: gratis per la
                     tastiera e per gli screen reader. */
                  <details className="group mt-3 rounded-lg border border-white/[0.06] bg-white/[0.03]">
                    <summary className="hud flex cursor-pointer list-none items-center justify-between p-3 text-slate-500 transition-colors hover:text-slate-300 [&::-webkit-details-marker]:hidden">
                      Angolo di business
                      <span
                        aria-hidden
                        className="text-phosphor transition-transform group-open:rotate-90"
                      >
                        ▸
                      </span>
                    </summary>
                    <p className="px-3 pb-3 text-sm leading-relaxed text-slate-400">
                      {idea.angle}
                    </p>
                  </details>
                )}
              </Section>
            )}

            <Section label="KPI" delayMs={120}>
              <div className="grid gap-2.5">
                {METRICS.map((m, i) => (
                  <MetricBar
                    key={m.label}
                    label={m.label}
                    value={idea[m.key] as number | null}
                    hint={METRIC_HINTS[m.label]}
                    delayMs={160 + i * 70}
                  />
                ))}
              </div>
            </Section>

            {history.length > 1 && (
              <Section label="Andamento del punteggio" delayMs={180}>
                <div className="flex items-end gap-4">
                  <AreaSpark
                    values={history.map((h) => h.composite)}
                    tone={sparkTone}
                    width={300}
                    height={64}
                    format={(v, i) =>
                      `${Math.round(v * 100)} · run #${history[i].run_id}`
                    }
                  />
                  <span className="pb-1 text-xs text-slate-500">
                    su {history.length} run
                  </span>
                </div>
              </Section>
            )}

            <Section label="La tua nota" delayMs={200}>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onBlur={saveNote}
                rows={3}
                placeholder="Appunti personali su questo segnale…"
                className="w-full resize-y rounded-xl border border-transparent bg-white/[0.03] px-3.5 py-2.5 text-sm leading-relaxed text-slate-200 transition-colors placeholder:text-slate-600 focus:border-phosphor/30 focus:bg-white/[0.05] focus:outline-none"
              />
              <div className="mt-2 flex items-center justify-between">
                <span
                  className={`text-xs ${
                    noteMutation.isError ? 'text-flare' : 'text-slate-600'
                  }`}
                >
                  {noteMutation.isPending
                    ? 'Salvataggio…'
                    : noteMutation.isError
                      ? 'Salvataggio fallito: il backend risponde? Riprova.'
                      : noteMutation.isSuccess
                        ? 'Nota salvata.'
                        : 'Si salva da sola quando esci dal campo. Vuota = cancellata.'}
                </span>
                <button
                  onClick={saveNote}
                  className="glass glass-hover rounded-lg px-2.5 py-1 text-xs font-medium text-phosphor"
                >
                  Salva nota
                </button>
              </div>
            </Section>

            <Section label={`Segnali (${idea.items.length})`} delayMs={240}>
              <ul className="grid gap-2">
                {idea.items.map((item, i) => (
                  <li
                    key={i}
                    className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/[0.06] transition-colors hover:ring-white/10"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm text-slate-200">{item.title}</p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          <span className="text-slate-400">{item.source}</span>
                          {item.author && ` · ${item.author}`}
                          {item.created_at && ` · ${dayMonthYear(item.created_at)}`}
                        </p>
                      </div>
                      {item.url && (
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                          className="glass glass-hover inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium text-phosphor"
                        >
                          Apri
                          <IconArrowUpRight />
                        </a>
                      )}
                    </div>
                    {item.engagement && Object.keys(item.engagement).length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {Object.entries(item.engagement).map(([k, v]) => (
                          <Badge
                            key={k}
                            className="bg-white/[0.03] text-slate-400 ring-white/[0.07]"
                          >
                            <span className="text-slate-500">{k}</span>{' '}
                            <span className="font-display tabular-nums">{v}</span>
                          </Badge>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Section>

            <footer className="hud flex items-center justify-between border-t border-white/[0.07] pt-4 text-slate-600">
              <span>primo contatto {dayMonthYear(idea.first_seen)}</span>
              <span>ultimo {dayMonthYear(idea.last_seen)}</span>
            </footer>
          </div>
        )}
      </aside>
    </div>
  )
}
