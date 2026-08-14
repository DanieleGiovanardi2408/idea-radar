import type {
  IdeaDetailOut,
  IdeaOut,
  OutcomesOut,
  PatchIdeaBody,
  ProfileOut,
  RhythmOut,
  RunOut,
  StatsOut,
  TopicOut,
  TrendOut,
  VideosOut,
  WorkspaceEntryOut,
  WorkspacePatch,
} from './types'

/** Base URL dell'API: vuota su web (il proxy Vite rende tutto same-origin),
 *  impostata alla build dell'app desktop, dove il backend gira su una porta
 *  locale propria (es. VITE_API_BASE=http://127.0.0.1:8765). */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''
const BASE = API_BASE

/** Un errore HTTP che si porta dietro lo stato: serve dove la UI deve dire
 *  cose diverse a seconda del codice, invece di un unico "non ha funzionato". */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path)
  if (!res.ok) throw new Error(`HTTP ${res.status} su ${path}`)
  return res.json() as Promise<T>
}

/** Una pagina di `/ideas`: le righe più il totale filtrato (X-Total-Count),
 *  così la UI può dire "N di T" invece di spacciare la pagina per l'archivio. */
export interface IdeaPage {
  rows: IdeaOut[]
  total: number
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
  ideas: async (params?: {
    status?: string
    topic_id?: number
    offset?: number
    limit?: number
    include_dismissed?: boolean
    profile?: string
    ungrouped?: boolean
    q?: string
  }): Promise<IdeaPage> => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.topic_id !== undefined) q.set('topic_id', String(params.topic_id))
    if (params?.offset !== undefined) q.set('offset', String(params.offset))
    if (params?.limit !== undefined) q.set('limit', String(params.limit))
    if (params?.include_dismissed) q.set('include_dismissed', 'true')
    if (params?.profile) q.set('profile', params.profile)
    if (params?.ungrouped) q.set('ungrouped', 'true')
    if (params?.q) q.set('q', params.q)
    const qs = q.toString()
    const path = `/ideas${qs ? `?${qs}` : ''}`
    const res = await fetch(BASE + path)
    if (!res.ok) throw new Error(`HTTP ${res.status} su ${path}`)
    const rows = (await res.json()) as IdeaOut[]
    const total = Number(res.headers.get('X-Total-Count') ?? rows.length)
    return { rows, total: Number.isFinite(total) ? total : rows.length }
  },
  idea: (id: number) => get<IdeaDetailOut>(`/ideas/${id}`),
  patchIdea: async (id: number, body: PatchIdeaBody): Promise<IdeaOut> => {
    const res = await fetch(`${BASE}/ideas/${id}`, {
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
  outcomes: () => get<OutcomesOut>('/outcomes'),
  workspace: () => get<WorkspaceEntryOut[]>('/workspace'),
  addToWorkspace: async (ideaId: number): Promise<WorkspaceEntryOut> => {
    const res = await fetch(`${BASE}/workspace/${ideaId}`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status} su /workspace/${ideaId}`)
    return res.json()
  },
  patchWorkspace: async (
    ideaId: number,
    body: WorkspacePatch,
  ): Promise<WorkspaceEntryOut> => {
    const res = await fetch(`${BASE}/workspace/${ideaId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status} su /workspace/${ideaId}`)
    return res.json()
  },
  generateWorkspaceMoves: async (ideaId: number): Promise<WorkspaceEntryOut> => {
    const res = await fetch(`${BASE}/workspace/${ideaId}/moves`, {
      method: 'POST',
    })
    // L'unico endpoint che distingue due fallimenti diversi, e l'utente deve
    // vederli diversi: 503 = Ollama giù (accendilo), 422 = ha risposto ma solo
    // mosse passe-partout, scartate dalla validazione (riprova, o è l'idea che
    // non si presta). Dire "Ollama è acceso?" nel secondo caso manda a cercare
    // il guasto dove non c'è.
    if (!res.ok) {
      throw new ApiError(res.status, `HTTP ${res.status} su /workspace/${ideaId}/moves`)
    }
    return res.json()
  },
  removeFromWorkspace: async (ideaId: number): Promise<void> => {
    const res = await fetch(`${BASE}/workspace/${ideaId}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`HTTP ${res.status} su /workspace/${ideaId}`)
  },
  stats: () => get<StatsOut>('/stats'),
  runs: () => get<RunOut[]>('/runs'),
  startRun: async (): Promise<{ started: boolean; detail: string }> => {
    const res = await fetch(`${BASE}/runs`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },
}
