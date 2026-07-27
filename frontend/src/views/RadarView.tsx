import { useMemo, useState } from 'react'
import { IdeaCard } from '../components/IdeaCard'
import { RadarScope } from '../components/RadarScope'
import { EmptyState, ErrorNotice, SkeletonCard } from '../components/ui'
import { useIdeas, useProfiles } from '../hooks/useRadarData'
import type { IdeaOut } from '../types'

type Filter = 'proposed' | 'all'

function matchesQuery(idea: IdeaOut, q: string): boolean {
  return (
    !q ||
    idea.label.toLowerCase().includes(q) ||
    (idea.why_text ?? '').toLowerCase().includes(q) ||
    (idea.topic_label ?? '').toLowerCase().includes(q)
  )
}

export function RadarView({ onSelect }: { onSelect: (id: number) => void }) {
  const [filter, setFilter] = useState<Filter>('proposed')
  const [showDismissed, setShowDismissed] = useState(false)
  const [query, setQuery] = useState('')
  // null = tutti i temi. Il filtro è server-side: il profilo vive sullo score.
  const [profile, setProfile] = useState<string | null>(null)

  const { data: profiles = [] } = useProfiles()
  const { data: ideas = [], isPending, isError } = useIdeas({ profile })
  // Le scartate vivono in una query a parte, caricata solo quando servono:
  // il server le esclude di default, con include_dismissed le riporta tutte.
  const dismissedQuery = useIdeas({
    includeDismissed: true,
    enabled: showDismissed,
    profile,
  })
  const dismissedIdeas = useMemo(
    () => (dismissedQuery.data ?? []).filter((i) => i.dismissed_at !== null),
    [dismissedQuery.data],
  )

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    const base = showDismissed
      ? dismissedIdeas
      : ideas.filter((i) => (filter === 'proposed' ? i.status === 'proposed' : true))
    return base.filter((i) => matchesQuery(i, q))
  }, [ideas, dismissedIdeas, showDismissed, filter, query])

  const proposedCount = ideas.filter((i) => i.status === 'proposed').length

  if (isError) {
    return <ErrorNotice>Impossibile caricare le idee dal backend.</ErrorNotice>
  }

  if (isPending && ideas.length === 0) {
    return (
      <div className="grid gap-3">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }

  /* Il selettore dei temi: i profili vengono da config.yaml sul backend, quindi
     compare solo se ce n'è più di uno da scegliere. */
  const themes =
    profiles.length > 1 ? (
      <div className="glass flex flex-wrap items-center gap-1.5 rounded-2xl p-2">
        <span className="hud px-1.5 text-slate-600">Tema</span>
        <button
          onClick={() => setProfile(null)}
          aria-pressed={profile === null}
          className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
            profile === null
              ? 'bg-phosphor/15 text-phosphor shadow-[inset_0_0_0_1px_rgba(46,232,162,0.25)]'
              : 'text-slate-500 hover:text-slate-300'
          }`}
        >
          Tutti
        </button>
        {profiles.map((p) => (
          <button
            key={p.name}
            onClick={() => setProfile(p.name)}
            aria-pressed={profile === p.name}
            title={p.keywords.join(' · ')}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
              profile === p.name
                ? 'bg-phosphor/15 text-phosphor shadow-[inset_0_0_0_1px_rgba(46,232,162,0.25)]'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {p.label}
            <span className="ml-1.5 tabular-nums opacity-60">{p.n_ideas}</span>
          </button>
        ))}
      </div>
    ) : null

  if (ideas.length === 0 && !showDismissed) {
    return (
      <div className="space-y-4">
        {themes}
        <EmptyState>
          {profile
            ? 'Nessuna idea in questo tema, per ora. Prova "Tutti".'
            : 'Il quadrante è vuoto: nessun segnale ancora intercettato. Lancia un run per iniziare la scansione.'}
        </EmptyState>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {themes}
      {/* Il quadrante: tutte le idee vive, qualunque sia il filtro della lista */}
      <RadarScope ideas={ideas} onSelect={onSelect} />

      <div className="glass flex flex-wrap items-center gap-3 rounded-2xl p-2">
        <div
          className={`flex rounded-xl bg-white/[0.03] p-0.5 transition-opacity duration-300 ${
            showDismissed ? 'pointer-events-none opacity-40' : ''
          }`}
        >
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
        {/* Le scartate: una modalità a parte, non un terzo filtro */}
        <div className="flex rounded-xl bg-white/[0.03] p-0.5">
          <button
            onClick={() => setShowDismissed((v) => !v)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-300 ${
              showDismissed
                ? 'bg-flare/15 text-flare shadow-[inset_0_0_0_1px_rgba(251,113,133,0.25)]'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            Scartate
            {dismissedQuery.data !== undefined && ` (${dismissedIdeas.length})`}
          </button>
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Cerca tra le idee…"
          className="min-w-0 flex-1 rounded-xl border border-transparent bg-white/[0.03] px-3.5 py-1.5 text-sm text-slate-200 transition-colors placeholder:text-slate-600 focus:border-phosphor/30 focus:bg-white/[0.05] focus:outline-none"
        />
      </div>

      {showDismissed && dismissedQuery.isError && (
        <ErrorNotice>Impossibile caricare le idee scartate.</ErrorNotice>
      )}

      {showDismissed && dismissedQuery.isPending ? (
        <div className="grid gap-3">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : visible.length === 0 ? (
        <EmptyState>
          {showDismissed
            ? 'Nessuna idea scartata: qui finiscono i segnali che archivi.'
            : 'Nessuna idea corrisponde a questi filtri.'}
        </EmptyState>
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
