/* Data layer: React Query al posto del monolite loadAll.
 *
 * Ogni risorsa ha la sua query (loading/error indipendenti) ma la cache è
 * condivisa: la stessa `useIdeas()` alimenta nav, radar e topic senza fetch
 * duplicate. Il polling replica il comportamento storico: mentre un run gira
 * tutto si aggiorna ogni 2s (il monitor è "live"), altrimenti niente. */

import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'
import type { IdeaDetailOut, PatchIdeaBody, StatsOut } from '../types'

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

export function useIdeas(opts?: { includeDismissed?: boolean; enabled?: boolean }) {
  const running = useIsRunning()
  const includeDismissed = opts?.includeDismissed ?? false
  return useQuery({
    queryKey: ['ideas', { includeDismissed }],
    queryFn: () =>
      api.ideas(includeDismissed ? { include_dismissed: true } : undefined),
    enabled: opts?.enabled ?? true,
    refetchInterval: liveInterval(running),
  })
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
    enabled: topicId !== null,
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

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.health })
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
    }
    prev.current = running
  }, [running, queryClient])
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

export function usePatchIdea() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: PatchIdeaBody }) =>
      api.patchIdea(id, body),
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
