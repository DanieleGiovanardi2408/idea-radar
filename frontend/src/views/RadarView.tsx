import { useMemo, useState } from 'react'
import { IdeaCard } from '../components/IdeaCard'
import { EmptyState } from '../components/ui'
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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-slate-800 bg-slate-900/60 p-0.5">
          {(
            [
              ['proposed', `Sopra soglia (${proposedCount})`],
              ['all', `Tutte (${ideas.length})`],
            ] as [Filter, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                filter === value
                  ? 'bg-slate-800 text-slate-100'
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
          className="min-w-0 flex-1 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-slate-700 focus:outline-none"
        />
      </div>

      {loading && ideas.length === 0 ? (
        <EmptyState>Caricamento delle idee…</EmptyState>
      ) : visible.length === 0 ? (
        <EmptyState>
          {ideas.length === 0
            ? 'Nessuna idea ancora. Lancia un run per popolare il radar.'
            : 'Nessuna idea corrisponde a questi filtri.'}
        </EmptyState>
      ) : (
        <div className="grid gap-3">
          {visible.map((idea) => (
            <IdeaCard key={idea.id} idea={idea} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  )
}
