import { useMemo, useState } from 'react'
import { IdeaCard } from '../components/IdeaCard'
import { RadarScope } from '../components/RadarScope'
import { EmptyState, ErrorNotice, SkeletonCard } from '../components/ui'
import { useDebounced } from '../hooks/useDebounced'
import { useIdeas, useProfiles } from '../hooks/useRadarData'

type Filter = 'proposed' | 'all'

export function RadarView({ onSelect }: { onSelect: (id: number) => void }) {
  const [filter, setFilter] = useState<Filter>('proposed')
  const [showDismissed, setShowDismissed] = useState(false)
  const [query, setQuery] = useState('')
  // null = tutti i temi. Il filtro è server-side: il profilo vive sullo score.
  const [profile, setProfile] = useState<string | null>(null)
  // La ricerca è server-side (etichetta, sommario, tema, su TUTTO l'archivio):
  // il debounce fa partire una query per pausa di digitazione, non per tasto.
  const q = useDebounced(query.trim(), 300)

  const { data: profiles = [] } = useProfiles()

  // Il quadrante mostra SEMPRE tutti i temi: selezionarne uno accende il suo
  // spicchio invece di far sparire gli altri — la mappa resta intera.
  const scopeQuery = useIdeas({})
  const scopeIdeas = scopeQuery.data?.rows ?? []

  // La lista: filtri, ricerca e paginazione li applica il server.
  const listQuery = useIdeas({
    profile,
    status: filter === 'proposed' ? 'proposed' : null,
    q,
  })
  // Le scartate vivono in una query a parte, caricata solo quando servono:
  // il server le esclude di default, con include_dismissed le riporta tutte.
  const dismissedQuery = useIdeas({
    includeDismissed: true,
    enabled: showDismissed,
    profile,
    q,
  })
  const dismissedIdeas = useMemo(
    () => (dismissedQuery.data?.rows ?? []).filter((i) => i.dismissed_at !== null),
    [dismissedQuery.data],
  )

  const active = showDismissed ? dismissedQuery : listQuery
  const visible = showDismissed ? dismissedIdeas : (listQuery.data?.rows ?? [])
  const total = listQuery.data?.total ?? 0
  const loaded = listQuery.data?.rows.length ?? 0

  if (scopeQuery.isError) {
    return <ErrorNotice>Impossibile caricare le idee dal backend.</ErrorNotice>
  }

  if (scopeQuery.isPending && scopeIdeas.length === 0) {
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

  if (scopeIdeas.length === 0 && !showDismissed && !q) {
    return (
      <div className="space-y-4">
        {themes}
        <EmptyState>
          Il quadrante è vuoto: nessun segnale ancora intercettato. Lancia un
          run per iniziare la scansione.
        </EmptyState>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {themes}
      {/* Il quadrante: tutte le idee vive, qualunque sia il filtro della lista.
          I profili configurati decidono gli spicchi; il tema selezionato accende
          il suo. */}
      <RadarScope
        ideas={scopeIdeas}
        onSelect={onSelect}
        profiles={profiles}
        activeProfile={profile}
      />

      <div className="glass flex flex-wrap items-center gap-3 rounded-2xl p-2">
        <div
          className={`flex rounded-xl bg-white/[0.03] p-0.5 transition-opacity duration-300 ${
            showDismissed ? 'pointer-events-none opacity-40' : ''
          }`}
        >
          {(
            [
              // Il conteggio è X-Total-Count del server, sull'intero archivio
              // filtrato: compare solo sul filtro attivo, l'unico di cui il
              // totale è noto senza una seconda query.
              ['proposed', 'Sopra soglia'],
              ['all', 'Tutte'],
            ] as [Filter, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              aria-pressed={filter === value}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-300 ${
                filter === value
                  ? 'bg-phosphor/15 text-phosphor shadow-[inset_0_0_0_1px_rgba(46,232,162,0.25)]'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {label}
              {filter === value && !showDismissed && (
                <span className="ml-1.5 tabular-nums opacity-60">{total}</span>
              )}
            </button>
          ))}
        </div>
        {/* Le scartate: una modalità a parte, non un terzo filtro */}
        <div className="flex rounded-xl bg-white/[0.03] p-0.5">
          <button
            onClick={() => setShowDismissed((v) => !v)}
            aria-pressed={showDismissed}
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
          aria-label="Cerca in tutto l'archivio"
          placeholder="Cerca in tutto l'archivio…"
          className="min-w-0 flex-1 rounded-xl border border-transparent bg-white/[0.03] px-3.5 py-1.5 text-sm text-slate-200 transition-colors placeholder:text-slate-600 focus:border-phosphor/30 focus:bg-white/[0.05] focus:outline-none"
        />
      </div>

      {showDismissed && dismissedQuery.isError && (
        <ErrorNotice>Impossibile caricare le idee scartate.</ErrorNotice>
      )}
      {!showDismissed && listQuery.isError && (
        <ErrorNotice>Impossibile caricare la lista dal backend.</ErrorNotice>
      )}

      {active.isPending ? (
        <div className="grid gap-3">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : visible.length === 0 ? (
        <EmptyState>
          {showDismissed
            ? 'Nessuna idea scartata: qui finiscono i segnali che archivi.'
            : q
              ? `Nessuna idea trovata per "${q}" in tutto l'archivio.`
              : 'Nessuna idea corrisponde a questi filtri.'}
        </EmptyState>
      ) : (
        <>
          <div className="grid gap-3">
            {visible.map((idea, index) => (
              <IdeaCard key={idea.id} idea={idea} index={index} onSelect={onSelect} />
            ))}
          </div>
          {/* Paginazione onesta: quante ne vedi, quante ce ne sono, e il resto
              si chiede al server invece di fingere che la pagina sia tutto. */}
          {active.hasNextPage && (
            <button
              onClick={() => active.fetchNextPage()}
              disabled={active.isFetchingNextPage}
              className="glass w-full rounded-2xl px-4 py-2.5 text-xs font-medium text-slate-400 transition-colors hover:text-phosphor disabled:opacity-50"
            >
              {active.isFetchingNextPage
                ? 'Carico…'
                : showDismissed
                  ? 'Carica altre'
                  : `Carica altre (${loaded} di ${total})`}
            </button>
          )}
        </>
      )}
    </div>
  )
}
