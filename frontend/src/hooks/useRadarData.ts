/* Data layer: React Query al posto del monolite loadAll.
 *
 * Ogni risorsa ha la sua query (loading/error indipendenti) ma la cache è
 * condivisa: la stessa `useIdeas()` alimenta nav, radar e topic senza fetch
 * duplicate. Il polling replica il comportamento storico: mentre un run gira
 * tutto si aggiorna ogni 2s (il monitor è "live"), altrimenti niente. */

import { useEffect, useRef } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { api } from '../api'
import type { IdeaPage } from '../api'
import type { IdeaDetailOut, IdeaOut, PatchIdeaBody, StatsOut } from '../types'

const LIVE_MS = 2000

function isRunning(stats: StatsOut | undefined): boolean {
  return stats?.last_run?.status === 'running'
}

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
    // Il polling si autoalimenta: finché l'ultimo run è in corso, 2s.
    refetchInterval: (query) => (isRunning(query.state.data) ? LIVE_MS : false),
  })
}

/** True se l'ultimo run è in corso. Condivide la cache di useStats. */
export function useIsRunning(): boolean {
  const { data } = useStats()
  return isRunning(data)
}

/* Le risorse "a valle" seguono il run live con lo stesso ritmo. */
function liveInterval(running: boolean): number | false {
  return running ? LIVE_MS : false
}

export const IDEAS_PAGE_SIZE = 100

/** Le idee, a pagine: filtri, ricerca e paginazione vivono sul server.

 *  Il dato esposto è già piatto (`rows` cumulative + `total` filtrato):
 *  `fetchNextPage` aggiunge la pagina successiva, e `total` viene da
 *  X-Total-Count, quindi la UI può dire "N di T" senza contare a occhio. */
export function useIdeas(opts?: {
  includeDismissed?: boolean
  enabled?: boolean
  /** Nome del profilo: mostra il radar dal punto di vista di un tema solo. */
  profile?: string | null
  /** Solo le proposte (sopra soglia), filtrate dal server. */
  status?: 'proposed' | null
  /** Ricerca full-archive, in SQL: etichetta, sommario, nome del tema. */
  q?: string
}) {
  const running = useIsRunning()
  const includeDismissed = opts?.includeDismissed ?? false
  const profile = opts?.profile ?? null
  const status = opts?.status ?? null
  const q = opts?.q?.trim() ?? ''
  return useInfiniteQuery({
    queryKey: ['ideas', { includeDismissed, profile, status, q }],
    queryFn: ({ pageParam }) =>
      api.ideas({
        offset: pageParam,
        limit: IDEAS_PAGE_SIZE,
        ...(includeDismissed ? { include_dismissed: true } : {}),
        ...(profile ? { profile } : {}),
        ...(status ? { status } : {}),
        ...(q ? { q } : {}),
      }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, page) => n + page.rows.length, 0)
      return loaded < last.total ? loaded : undefined
    },
    select: (data) => ({
      rows: data.pages.flatMap((page) => page.rows),
      total: data.pages[0]?.total ?? 0,
    }),
    enabled: opts?.enabled ?? true,
    refetchInterval: liveInterval(running),
  })
}

/** I temi configurati sul backend (config.yaml è l'unica fonte di verità). */
export function useProfiles() {
  return useQuery({ queryKey: ['profiles'], queryFn: api.profiles })
}

export type TopicOrder = 'top_composite' | 'n_ideas' | 'last_seen'

export function useTopics(opts?: { minIdeas?: number; orderBy?: TopicOrder }) {
  const running = useIsRunning()
  const minIdeas = opts?.minIdeas ?? 1
  const orderBy = opts?.orderBy ?? 'top_composite'
  return useQuery({
    queryKey: ['topics', { minIdeas, orderBy }],
    queryFn: () => api.topics({ minIdeas, orderBy }),
    refetchInterval: liveInterval(running),
  })
}

/** Le idee di UN topic, chieste al server invece di filtrare la lista globale.
 *
 *  `/ideas` è paginato (100 di default): con più di mille idee in archivio
 *  filtrare in locale mostrerebbe solo quelle dei topic in cima. */
export function useTopicIdeas(topicId: number | null) {
  const running = useIsRunning()
  return useQuery({
    queryKey: ['ideas', { topicId }],
    queryFn: () => api.ideas({ topic_id: topicId as number }),
    select: (page) => page.rows,
    enabled: topicId !== null,
    refetchInterval: liveInterval(running),
  })
}

/** Le idee che non stanno in nessun tema, di un profilo o di tutti.
 *
 * Da quando un'idea sola non apre un topic sono la maggioranza dell'archivio:
 * senza un modo per chiederle, la vista Topic ne mostrerebbe una minoranza e
 * farebbe sparire il resto. `enabled` le chiede solo quando la sezione si apre.
 */
export function useUngroupedIdeas(profile: string | null, enabled: boolean) {
  const running = useIsRunning()
  return useQuery({
    queryKey: ['ideas', { ungrouped: true, profile }],
    queryFn: () =>
      api.ideas({ ungrouped: true, ...(profile ? { profile } : {}) }),
    select: (page) => page.rows,
    enabled,
    refetchInterval: liveInterval(running),
  })
}

export function useTrends() {
  const running = useIsRunning()
  return useQuery({
    queryKey: ['trends'],
    queryFn: api.trends,
    refetchInterval: liveInterval(running),
  })
}

/** Il track record: verdetti sulle proposte passate. Cambia solo a fine run. */
export function useOutcomes() {
  return useQuery({ queryKey: ['outcomes'], queryFn: api.outcomes })
}

/** Storico run completo: si carica solo quando il Monitor lo apre. */
export function useRuns(opts?: { enabled?: boolean }) {
  const running = useIsRunning()
  return useQuery({
    queryKey: ['runs'],
    queryFn: api.runs,
    enabled: opts?.enabled ?? true,
    refetchInterval: liveInterval(running),
  })
}

/** Ritmo dei segnali. Cambia solo quando arrivano item nuovi: niente polling. */
export function useRhythm(days = 28) {
  const running = useIsRunning()
  return useQuery({
    queryKey: ['rhythm', days],
    queryFn: () => api.rhythm(days),
    refetchInterval: liveInterval(running),
  })
}

/** Video in tendenza sui temi. Il backend ha già una cache di 15 minuti:
 *  qui basta non rifetchare a ogni rimontaggio del pannello. */
export function useVideos(opts?: { limit?: number; live?: boolean }) {
  const limit = opts?.limit ?? 6
  const live = opts?.live ?? false
  return useQuery({
    queryKey: ['videos', { limit, live }],
    queryFn: () => api.videos({ limit, live }),
    staleTime: 10 * 60 * 1000,
  })
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    // Nell'app desktop il backend parte INSIEME alla finestra: per qualche
    // secondo non risponde. Finché è giù si ritenta piano, e la UI si
    // sblocca da sola appena arriva — niente "ricarica la pagina".
    refetchInterval: (query) =>
      query.state.data?.status === 'ok' ? false : 1500,
  })
}

export function useIdea(id: number) {
  return useQuery({ queryKey: ['idea', id], queryFn: () => api.idea(id) })
}

/** Da montare una volta sola (in App): quando lo stato passa da running a
 *  non-running il run ha prodotto dati nuovi, quindi rinfresca tutto il resto. */
export function useRunWatcher(): boolean {
  const queryClient = useQueryClient()
  const running = useIsRunning()
  const prev = useRef(running)
  useEffect(() => {
    if (prev.current && !running) {
      queryClient.invalidateQueries({ queryKey: ['ideas'] })
      queryClient.invalidateQueries({ queryKey: ['topics'] })
      queryClient.invalidateQueries({ queryKey: ['trends'] })
      queryClient.invalidateQueries({ queryKey: ['idea'] })
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['outcomes'] })
    }
    prev.current = running
  }, [running, queryClient])

  // Quando il backend passa da giù a su (avvio dell'app desktop, o riavvio
  // in dev), le query fallite nel frattempo vanno rifatte tutte: senza,
  // resterebbero in errore finché qualcuno non ricarica a mano.
  const online = useHealth().data?.status === 'ok'
  const wasOnline = useRef(online)
  useEffect(() => {
    if (!wasOnline.current && online) {
      queryClient.invalidateQueries()
    }
    wasOnline.current = online
  }, [online, queryClient])

  return running
}

export function useStartRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.startRun,
    // Basta invalidare stats: il refetchInterval condizionale fa il resto.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

/** L'effetto locale di un PATCH, applicato subito senza aspettare il server.
 *
 *  Generica sui tre campi di stato utente, perché va applicata sia al dossier
 *  (`IdeaDetailOut`) sia alle righe delle liste (`IdeaOut`): il pin cliccato su
 *  una card deve accendersi lì, non solo dentro il dettaglio. */
function optimistic<T extends Pick<IdeaOut, 'pinned' | 'dismissed_at' | 'note'>>(
  prev: T,
  body: PatchIdeaBody,
): T {
  const next = { ...prev }
  if (body.pinned !== undefined) next.pinned = body.pinned
  if (body.dismissed !== undefined) {
    next.dismissed_at = body.dismissed ? new Date().toISOString() : null
  }
  if (body.note !== undefined) next.note = body.note
  return next
}

/** Le forme grezze sotto la chiave ['ideas', …]: pagina singola o infinita. */
type IdeasCache = IdeaPage | { pages: IdeaPage[]; pageParams: unknown[] }

function patchRows(rows: IdeaOut[], id: number, body: PatchIdeaBody): IdeaOut[] {
  return rows.map((row) => (row.id === id ? optimistic(row, body) : row))
}

function patchIdeasCache(data: IdeasCache, id: number, body: PatchIdeaBody): IdeasCache {
  if ('pages' in data) {
    return {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        rows: patchRows(page.rows, id, body),
      })),
    }
  }
  return { ...data, rows: patchRows(data.rows, id, body) }
}

/** Azioni utente su un'idea, con effetto immediato in UI.
 *
 *  Ogni chiamata a questo hook è una mutation INDIPENDENTE: va usata una per
 *  concetto (pin, scarta, nota), non una condivisa. Con una sola, `isPending`
 *  di un'azione bloccava i pulsanti delle altre — e la PATCH automatica di
 *  "visto" all'apertura rendeva il dossier di sola lettura per tutta la sua
 *  durata, cioè esattamente quando l'utente ci clicca sopra.
 *
 *  L'aggiornamento è ottimistico: il pin si accende subito e torna indietro se
 *  il server rifiuta. Su un backend impegnato da un run è la differenza tra
 *  "reattivo" e "rotto". */
export function usePatchIdea() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: PatchIdeaBody }) =>
      api.patchIdea(id, body),
    onMutate: async ({ id, body }) => {
      await queryClient.cancelQueries({ queryKey: ['idea', id] })
      const previous = queryClient.getQueryData<IdeaDetailOut>(['idea', id])
      if (previous) {
        queryClient.setQueryData<IdeaDetailOut>(
          ['idea', id],
          optimistic(previous, body),
        )
      }
      // Tutte le liste in cache (radar, scartate, per topic) in un colpo.
      // In cache vivono due forme GREZZE (`select` agisce solo in uscita):
      // le query semplici tengono una IdeaPage, le infinite {pages, pageParams}.
      const lists = queryClient.getQueriesData<IdeasCache>({ queryKey: ['ideas'] })
      for (const [key, data] of lists) {
        if (!data) continue
        queryClient.setQueryData<IdeasCache>(key, patchIdeasCache(data, id, body))
      }
      return { previous, lists }
    },
    onError: (_error, { id }, context) => {
      // Rollback: meglio tornare visibilmente indietro che mentire.
      if (context?.previous) {
        queryClient.setQueryData(['idea', id], context.previous)
      }
      for (const [key, list] of context?.lists ?? []) {
        queryClient.setQueryData(key, list)
      }
    },
    onSuccess: (updated) => {
      // Il PATCH risponde l'IdeaOut aggiornata: fondila nel dettaglio in cache
      // (che in più ha la history) e lascia che le liste si rifacciano da sole
      // — così anche l'ordinamento server (pinnate prima) resta autorevole.
      queryClient.setQueryData<IdeaDetailOut>(['idea', updated.id], (prev) =>
        prev ? { ...prev, ...updated } : prev,
      )
      queryClient.invalidateQueries({ queryKey: ['ideas'] })
    },
  })
}

/** Segna l'idea come vista: automatica all'apertura, quindi silenziosa.
 *
 *  Separata di proposito da ``usePatchIdea``: non deve né disabilitare i
 *  comandi dell'utente né invalidare le liste (nessuna vista dipende da
 *  ``seen_at``), altrimenti aprire un dossier costa un refetch di tutto. */
export function useMarkSeen() {
  return useMutation({
    mutationFn: (id: number) => api.patchIdea(id, { seen: true }),
  })
}
