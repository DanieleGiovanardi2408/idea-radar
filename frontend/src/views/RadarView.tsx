import { useMemo, useState } from 'react'
import { IdeaCard } from '../components/IdeaCard'
import { RadarScope } from '../components/RadarScope'
import { EmptyState, SkeletonCard } from '../components/ui'
import type { IdeaOut } from '../types'

type Filter = 'proposed' | 'all'

export function RadarView({
  ideas,
  loading,
  onSelect,
}: {
  ideas: IdeaOut[]
  loading: boolean
  onSelect: (id: number) => void
}) {
  const [filter, setFilter] = useState<Filter>('proposed')
  const [query, setQuery] = useState('')

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return ideas
      .filter((i) => (filter === 'proposed' ? i.status === 'proposed' : true))
      .filter(
        (i) =>
          !q ||
          i.label.toLowerCase().includes(q) ||
          (i.why_text ?? '').toLowerCase().includes(q) ||
          (i.topic_label ?? '').toLowerCase().includes(q),
      )
  }, [ideas, filter, query])

  const proposedCount = ideas.filter((i) => i.status === 'proposed').length

  if (loading && ideas.length === 0) {
    return (
      <div className="grid gap-3">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }

  if (ideas.length === 0) {
    return (
      <EmptyState>
        Il quadrante è vuoto: nessun segnale ancora intercettato.
        <br />
        Lancia un run per iniziare la scansione.
      </EmptyState>
    )
  }

  return (
    <div className="space-y-4">
      {/* Il quadrante: tutte le idee vive, qualunque sia il filtro della lista */}
      <RadarScope ideas={ideas} onSelect={onSelect} />

      <div className="glass flex flex-wrap items-center gap-3 rounded-2xl p-2">
        <div className="flex rounded-xl bg-white/[0.03] p-0.5">
          {(
            [
              ['proposed', `Sopra soglia (${proposedCount})`],
              ['all', `Tutte (${ideas.length})`],
            ] as [Filter, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-300 ${
                filter === value
                  ? 'bg-phosphor/15 text-phosphor shadow-[inset_0_0_0_1px_rgba(46,232,162,0.25)]'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Cerca tra le idee…"
          className="min-w-0 flex-1 rounded-xl border border-transparent bg-white/[0.03] px-3.5 py-1.5 text-sm text-slate-200 transition-colors placeholder:text-slate-600 focus:border-phosphor/30 focus:bg-white/[0.05] focus:outline-none"
        />
      </div>

      {visible.length === 0 ? (
        <EmptyState>Nessuna idea corrisponde a questi filtri.</EmptyState>
      ) : (
        <div className="grid gap-3">
          {visible.map((idea, index) => (
            <IdeaCard key={idea.id} idea={idea} index={index} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  )
}
