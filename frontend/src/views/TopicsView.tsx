import { useState, type CSSProperties } from 'react'
import { useParams } from 'react-router-dom'
import { IdeaCard } from '../components/IdeaCard'
import { staggerDelay } from '../components/motion'
import {
  Badge,
  EmptyState,
  ErrorNotice,
  IconChevron,
  ScoreRing,
  SkeletonCard,
} from '../components/ui'
import {
  useProfiles,
  useTopicIdeas,
  useTopics,
  useUngroupedIdeas,
  type TopicOrder,
} from '../hooks/useRadarData'
import type { TopicOut } from '../types'

const ORDERS: { key: TopicOrder; label: string }[] = [
  { key: 'top_composite', label: 'Punteggio' },
  { key: 'n_ideas', label: 'Dimensione' },
  { key: 'last_seen', label: 'Recenti' },
]

/* Il corpo della fisarmonica chiede al server le idee DEL topic: `/ideas` è
 * paginato, quindi filtrare la lista globale mostrerebbe solo le idee dei topic
 * in cima alla classifica. Il fetch parte solo all'apertura. */
function TopicMembers({
  topicId,
  onSelect,
}: {
  topicId: number
  onSelect: (id: number) => void
}) {
  const { data: members = [], isPending, isError } = useTopicIdeas(topicId)

  if (isError) {
    return (
      <p className="p-3 text-sm text-flare">
        Impossibile caricare le idee di questo topic.
      </p>
    )
  }
  if (isPending) {
    return <p className="p-3 text-sm text-slate-600">Caricamento delle idee…</p>
  }
  if (members.length === 0) {
    return (
      <p className="p-3 text-sm text-slate-500">
        Nessuna idea viva in questo topic.
      </p>
    )
  }
  return (
    <>
      {members.map((idea, index) => (
        <IdeaCard key={idea.id} idea={idea} index={index} onSelect={onSelect} />
      ))}
    </>
  )
}

/* Le idee che non stanno in nessun tema.
 *
 * Un'idea sola non apre un topic — misurato sull'archivio, con questi embedding
 * il vicino più prossimo (mediana 0,791) è indistinguibile da una coppia a caso
 * (99° percentile 0,750, punte a 0,878), quindi i "temi da un elemento" erano
 * 784 su 1002 e non descrivevano niente. Ma non essere raggruppata non vuol dire
 * non esistere: qui sotto ci sono, sotto il loro macro-tema, in una sezione che
 * si apre solo se la si chiede. */
function UngroupedRow({
  profile,
  label,
  count,
  index,
  open,
  onToggle,
  onSelect,
}: {
  profile: string | null
  label: string
  count: number
  index: number
  open: boolean
  onToggle: () => void
  onSelect: (id: number) => void
}) {
  const { data: ideas = [], isPending, isError } = useUngroupedIdeas(profile, open)
  return (
    <div
      className="stagger overflow-hidden rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.01]"
      style={staggerDelay(index) as CSSProperties}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="group flex w-full items-center gap-3 p-4 text-left"
      >
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-display text-sm font-medium tracking-tight text-slate-400 group-hover:text-slate-200">
            Non raggruppate in {label}
          </h3>
          <p className="mt-1 text-xs text-slate-600">
            {count} idee che non somigliano abbastanza a nessun'altra per fare
            tema. Restano ordinate per punteggio.
          </p>
        </div>
        <IconChevron open={open} />
      </button>
      {open && (
        <div className="grid gap-2 border-t border-white/[0.06] p-2">
          {isError && (
            <p className="p-3 text-sm text-flare">
              Impossibile caricare le idee non raggruppate.
            </p>
          )}
          {isPending && !isError && (
            <p className="p-3 text-sm text-slate-600">Caricamento…</p>
          )}
          {ideas.map((idea, i) => (
            <IdeaCard key={idea.id} idea={idea} index={i} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  )
}

function TopicRow({
  topic,
  index,
  open,
  onToggle,
  onSelect,
}: {
  topic: TopicOut
  index: number
  open: boolean
  onToggle: () => void
  onSelect: (id: number) => void
}) {
  const proposedRatio = topic.n_ideas > 0 ? topic.n_proposed / topic.n_ideas : 0
  return (
    <div
      className={`glass stagger overflow-hidden rounded-2xl transition-colors duration-300 ${
        open ? 'border-phosphor/25' : ''
      }`}
      style={staggerDelay(index) as CSSProperties}
    >
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="group flex w-full items-center gap-4 p-4 text-left"
      >
        <ScoreRing value={topic.top_composite} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-display font-medium tracking-tight text-slate-100 group-hover:text-white">
            {topic.label}
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Badge className="bg-white/[0.03] text-slate-400 ring-white/[0.07]">
              {topic.n_ideas} idee
            </Badge>
            <Badge className="bg-white/[0.03] text-slate-400 ring-white/[0.07]">
              {topic.n_items} segnali
            </Badge>
            {topic.n_proposed > 0 && (
              <Badge className="bg-phosphor/10 text-phosphor ring-phosphor/30">
                {topic.n_proposed} sopra soglia
              </Badge>
            )}
            <span className="text-xs text-slate-600">
              media {Math.round(topic.avg_composite * 100)}
            </span>
          </div>
          {/* quota di idee sopra soglia nel topic */}
          <div className="mt-2.5 h-1 w-full max-w-56 overflow-hidden rounded-full bg-white/[0.05]">
            <div
              className="grow-bar h-full rounded-full bg-gradient-to-r from-phosphor/40 to-phosphor"
              style={{
                width: `${Math.max(proposedRatio * 100, topic.n_proposed > 0 ? 8 : 0)}%`,
                animationDelay: `${index * 60}ms`,
              }}
            />
          </div>
        </div>
        <IconChevron
          open={open}
          className="shrink-0 text-slate-600 group-hover:text-phosphor/70"
        />
      </button>
      {/* fisarmonica fluida senza misurare le altezze */}
      <div
        className="grid transition-[grid-template-rows] duration-400 ease-out"
        style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <div className="grid gap-2 border-t border-white/[0.06] p-3">
            {open && <TopicMembers topicId={topic.id} onSelect={onSelect} />}
          </div>
        </div>
      </div>
    </div>
  )
}

export function TopicsView({ onSelect }: { onSelect: (id: number) => void }) {
  // Deep link dalla vista Trend: /topics/:topicId apre quel tema.
  const { topicId } = useParams()
  const focused = topicId !== undefined ? Number(topicId) : NaN
  const [openId, setOpenId] = useState<number | null>(
    Number.isInteger(focused) ? focused : null,
  )
  // Una sola sezione "non raggruppate" aperta per volta, come per i temi:
  // sono centinaia di idee, aprirne due insieme è una lista da scorrere a vuoto.
  const [openUngrouped, setOpenUngrouped] = useState<string | null>(null)
  const [order, setOrder] = useState<TopicOrder>('n_ideas')
  // Di default si nascondono i temi da una sola idea: con le soglie tarate sono
  // la maggioranza, sono veri, ma scorrerne centinaia non serve a niente.
  const [onlyGroups, setOnlyGroups] = useState(true)
  // Un tema aperto da deep link può essere da una idea sola: il filtro lo
  // nasconderebbe proprio mentre lo si stava cercando, quindi arrivando dal
  // Trend si mostrano tutti.
  const filtering = onlyGroups && !Number.isInteger(focused)

  const { data: profiles = [] } = useProfiles()
  const { data: topics = [], isPending, isError } = useTopics({
    minIdeas: filtering ? 2 : 1,
    orderBy: order,
  })

  if (isError) {
    return <ErrorNotice>Impossibile caricare i topic dal backend.</ErrorNotice>
  }

  const controls = (
    <div className="flex flex-wrap items-center justify-between gap-3 px-1">
      <div className="flex items-center gap-1.5">
        <span className="hud text-slate-600">Ordina</span>
        {ORDERS.map((o) => (
          <button
            key={o.key}
            onClick={() => setOrder(o.key)}
            aria-pressed={order === o.key}
            className={`rounded-full px-2.5 py-1 text-[11px] ring-1 transition-colors ${
              order === o.key
                ? 'bg-phosphor/10 text-phosphor ring-phosphor/30'
                : 'text-slate-500 ring-white/[0.07] hover:text-slate-300'
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      <button
        onClick={() => setOnlyGroups((v) => !v)}
        aria-pressed={filtering}
        className={`rounded-full px-2.5 py-1 text-[11px] ring-1 transition-colors ${
          filtering
            ? 'bg-white/[0.04] text-slate-300 ring-white/[0.09]'
            : 'text-slate-500 ring-white/[0.07] hover:text-slate-300'
        }`}
      >
        {filtering ? 'Solo temi con più idee' : 'Tutti i temi, anche da una idea'}
      </button>
    </div>
  )

  if (isPending) {
    return (
      <div className="grid gap-3">
        {controls}
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }

  /* Zero topic NON significa zero idee: da quando un'idea sola non apre un tema,
     un profilo può avere centinaia di idee e nessun gruppo. Se ci sono non
     raggruppate da mostrare, la vista deve proseguire fino alle sezioni invece
     di dichiarare il vuoto — è il caso normale subito dopo `prune-topics`. */
  const totaleNonRaggruppate = profiles.reduce(
    (somma, p) => somma + (p.n_ungrouped ?? 0),
    0,
  )
  if (topics.length === 0 && totaleNonRaggruppate === 0) {
    return (
      <div className="grid gap-3">
        {controls}
        <EmptyState>
          {filtering ? (
            <>
              Nessun tema raccoglie più di un'idea. Togli il filtro per vedere
              anche quelli da una sola.
            </>
          ) : (
            <>
              Nessun topic. Il raggruppamento richiede gli embedding: assicurati di
              aver fatto{' '}
              <code className="text-phosphor/80">ollama pull nomic-embed-text</code> e
              lancia un run.
            </>
          )}
        </EmptyState>
      </div>
    )
  }

  /* Due livelli: il macro-tema viene dai profili (dichiarato in config.yaml), il
     micro dal clustering semantico. Raggruppare qui è l'unica cosa che serviva:
     la gerarchia esiste già nei dati, era solo appiattita nella vista. */
  const groups = profiles
    .map((p) => ({
      name: p.name,
      label: p.label,
      topics: topics.filter((t) => t.profile === p.name),
      ungrouped: p.n_ungrouped ?? 0,
    }))
    // Un macro-tema entra anche se ha SOLO idee non raggruppate: nasconderlo
    // significherebbe far sparire dalla vista la parte più grossa dell'archivio.
    .filter((g) => g.topics.length > 0 || g.ungrouped > 0)
  const orphans = topics.filter(
    (t) => !t.profile || !profiles.some((p) => p.name === t.profile),
  )
  if (orphans.length > 0) {
    groups.push({
      name: '',
      label: 'Senza tema',
      topics: orphans,
      ungrouped: 0,
    })
  }

  let rendered = 0
  return (
    <div className="grid gap-3">
      {controls}
      {groups.map((group) => {
        const ideas = group.topics.reduce((sum, t) => sum + t.n_ideas, 0)
        return (
          <section key={group.name || 'orphans'} className="grid gap-2">
            <h2 className="flex items-baseline gap-2 px-1 pt-2">
              <span className="font-display text-sm font-semibold tracking-tight text-slate-300">
                {group.label}
              </span>
              <span className="text-xs tabular-nums text-slate-600">
                {group.topics.length} temi · {ideas} idee
                {group.ungrouped > 0 && ` · ${group.ungrouped} non raggruppate`}
              </span>
            </h2>
            {group.topics.map((topic) => (
              <TopicRow
                key={topic.id}
                topic={topic}
                index={rendered++}
                open={openId === topic.id}
                onToggle={() => setOpenId(openId === topic.id ? null : topic.id)}
                onSelect={onSelect}
              />
            ))}
            {group.ungrouped > 0 && group.name && (
              <UngroupedRow
                profile={group.name}
                label={group.label}
                count={group.ungrouped}
                index={rendered++}
                open={openUngrouped === group.name}
                onToggle={() =>
                  setOpenUngrouped(
                    openUngrouped === group.name ? null : group.name,
                  )
                }
                onSelect={onSelect}
              />
            )}
          </section>
        )
      })}
    </div>
  )
}
