import type {
  IdeaDetailOut,
  IdeaOut,
  PatchIdeaBody,
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
  ideas: (params?: {
    status?: string
    topic_id?: number
    offset?: number
    include_dismissed?: boolean
  }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.topic_id !== undefined) q.set('topic_id', String(params.topic_id))
    if (params?.offset !== undefined) q.set('offset', String(params.offset))
    if (params?.include_dismissed) q.set('include_dismissed', 'true')
    const qs = q.toString()
    return get<IdeaOut[]>(`/ideas${qs ? `?${qs}` : ''}`)
  },
  idea: (id: number) => get<IdeaDetailOut>(`/ideas/${id}`),
  patchIdea: async (id: number, body: PatchIdeaBody): Promise<IdeaOut> => {
    const res = await fetch(`/ideas/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status} su /ideas/${id}`)
    return res.json()
  },
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
