import type {
  IdeaDetailOut,
  IdeaOut,
  PatchIdeaBody,
  ProfileOut,
  RhythmOut,
  RunOut,
  StatsOut,
  TopicOut,
  TrendOut,
  VideosOut,
} from './types'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`HTTP ${res.status} su ${path}`)
  return res.json() as Promise<T>
}

export const api = {
  health: () => get<{ status: string }>('/health'),
  profiles: () => get<ProfileOut[]>('/profiles'),
  rhythm: (days = 28) => get<RhythmOut>(`/rhythm?days=${days}`),
  videos: (params?: { limit?: number; live?: boolean }) => {
    const q = new URLSearchParams()
    if (params?.limit !== undefined) q.set('limit', String(params.limit))
    if (params?.live) q.set('live', 'true')
    const qs = q.toString()
    return get<VideosOut>(`/videos${qs ? `?${qs}` : ''}`)
  },
  ideas: (params?: {
    status?: string
    topic_id?: number
    offset?: number
    include_dismissed?: boolean
    profile?: string
  }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.topic_id !== undefined) q.set('topic_id', String(params.topic_id))
    if (params?.offset !== undefined) q.set('offset', String(params.offset))
    if (params?.include_dismissed) q.set('include_dismissed', 'true')
    if (params?.profile) q.set('profile', params.profile)
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
  topics: (params?: { minIdeas?: number; orderBy?: string }) => {
    const q = new URLSearchParams()
    if (params?.minIdeas !== undefined) q.set('min_ideas', String(params.minIdeas))
    if (params?.orderBy) q.set('order_by', params.orderBy)
    const qs = q.toString()
    return get<TopicOut[]>(`/topics${qs ? `?${qs}` : ''}`)
  },
  trends: () => get<TrendOut[]>('/trends'),
  stats: () => get<StatsOut>('/stats'),
  runs: () => get<RunOut[]>('/runs'),
  startRun: async (): Promise<{ started: boolean; detail: string }> => {
    const res = await fetch('/runs', { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
}
