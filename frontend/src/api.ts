import type {
  IdeaDetailOut,
  IdeaOut,
  RunOut,
  StatsOut,
  TopicOut,
  TrendOut,
} from './types'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`HTTP ${res.status} su ${path}`)
  return res.json() as Promise<T>
}

export const api = {
  health: () => get<{ status: string }>('/health'),
  ideas: (params?: { status?: string; topic_id?: number }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.topic_id !== undefined) q.set('topic_id', String(params.topic_id))
    const qs = q.toString()
    return get<IdeaOut[]>(`/ideas${qs ? `?${qs}` : ''}`)
  },
  idea: (id: number) => get<IdeaDetailOut>(`/ideas/${id}`),
  topics: () => get<TopicOut[]>('/topics'),
  trends: () => get<TrendOut[]>('/trends'),
  stats: () => get<StatsOut>('/stats'),
  runs: () => get<RunOut[]>('/runs'),
  startRun: async (): Promise<{ started: boolean; detail: string }> => {
    const res = await fetch('/runs', { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
}
