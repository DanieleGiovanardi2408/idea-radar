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
  useTopicIdeas,
  useTopics,
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
  const [order, setOrder] = useState<TopicOrder>('n_ideas')
  // Di default si nascondono i temi da una sola idea: con le soglie tarate sono
  // la maggioranza, sono veri, ma scorrerne centinaia non serve a niente.
  const [onlyGroups, setOnlyGroups] = useState(true)
  // Un tema aperto da deep link può essere da una idea sola: il filtro lo
  // nasconderebbe proprio mentre lo si stava cercando, quindi arrivando dal
  // Trend si mostrano tutti.
  const filtering = onlyGroups && !Number.isInteger(focused)

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

  if (topics.length === 0) {
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

  return (
    <div className="grid gap-3">
      {controls}
      {topics.map((topic, index) => (
        <TopicRow
          key={topic.id}
          topic={topic}
          index={index}
          open={openId === topic.id}
          onToggle={() => setOpenId(openId === topic.id ? null : topic.id)}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}
