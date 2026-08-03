export type ItemOut = {
  source: string
  title: string
  url: string | null
  author: string | null
  created_at: string | null
  engagement: Record<string, number> | null
}

export type IdeaOut = {
  id: number
  label: string
  summary: string | null
  status: string
  topic_id: number | null
  topic_label: string | null
  composite: number
  heat: number | null
  credibility: number | null
  feasibility: number | null
  opportunity: number | null
  fit: number | null
  /** Profilo (macro-tema) su cui il fit è stato misurato; null se nessun tema
   *  reclama l'idea. */
  profile: string | null
  why_text: string | null
  difficulty: string | null
  n_items: number
  first_seen: string | null
  last_seen: string | null
  items: ItemOut[]
  /* Campi utente: annotazioni personali, sopravvivono ai run */
  pinned: boolean
  dismissed_at: string | null
  seen_at: string | null
  note: string | null
  /* Il "cosa fartene", generato quando l'idea supera la soglia: mosse concrete
     per sfruttare il vantaggio e (solo per le idee in cima) il mini-caso di
     business. null = non ancora generati (arrivano al prossimo run). */
  moves: string[] | null
  angle: string | null
}

/* Body di PATCH /ideas/{id}: i campi assenti non vengono toccati;
   `note: null` cancella la nota, `dismissed` scarta o ripristina. */
export type PatchIdeaBody = {
  pinned?: boolean
  dismissed?: boolean
  seen?: boolean
  note?: string | null
}

export type ScorePoint = {
  run_id: number
  composite: number
  heat: number
  credibility: number
  feasibility: number
  opportunity: number
  fit: number
}

export type IdeaDetailOut = IdeaOut & { history: ScorePoint[] }

export type TopicOut = {
  id: number
  label: string
  /** Macro-tema: il profilo della maggioranza delle idee del topic. */
  profile: string | null
  n_ideas: number
  n_items: number
  n_proposed: number
  avg_composite: number
  top_composite: number
  first_seen: string
  last_seen: string
}

export type TrendPoint = {
  run_id: number
  started_at: string
  n_ideas: number
  n_items: number
  avg_composite: number
}

export type TrendOut = {
  topic_id: number
  label: string
  points: TrendPoint[]
  n_ideas: number
  avg_composite: number
  delta_ideas: number
  delta_composite: number
}

export type RunOut = {
  id: number
  started_at: string
  finished_at: string | null
  status: string
  phase: string
  n_items: number
  n_items_fetched: number
  n_items_new: number
  n_ideas_processed: number
  n_ideas_proposed: number
  n_ideas_total: number
  n_topics: number
  error: string | null
  /* `requests`/`failed_queries` li riporta solo chi paga un rate limit (oggi
     GitHub): dicono che una fonte è riuscita *a metà*, cosa che prima nessun
     numero rappresentava — le fasce d'età perse contavano come successo. */
  sources: Record<
    string,
    {
      fetched: number
      new: number
      error?: string
      requests?: number
      failed_queries?: number
      waited_seconds?: number
    }
  > | null
}

export type StatsOut = {
  n_items: number
  n_ideas: number
  n_topics: number
  n_proposed: number
  n_archived: number
  n_runs: number
  items_by_source: Record<string, number>
  last_run: RunOut | null
  recent_runs: RunOut[]
}

/** Un tema del radar, dichiarato in config.yaml sul backend. */
export interface ProfileOut {
  name: string
  label: string
  keywords: string[]
  n_ideas: number
  /* Quante di quelle idee non stanno in nessun tema. Non si ricava per
     differenza da `n_ideas` e dalla somma dei topic: i due si contano su
     insiemi diversi, quindi la sottrazione mentirebbe. */
  n_ungrouped: number
}

/** Ritmo dei segnali: quando NASCONO (created_at), non quando li raccogliamo. */
export interface RhythmOut {
  days: number
  n_items: number
  n_without_date: number
  /** 7 righe (lunedì = 0) x 24 colonne. */
  grid: number[][]
  peak: number
  by_source: Record<string, number>
}

export interface VideoOut {
  video_id: string
  title: string
  channel: string
  published_at: string
  thumbnail: string
  live: boolean
  profile: string | null
  url: string
  embed_url: string
}

export interface VideosOut {
  /** False = manca YOUTUBE_API_KEY: il pannello lo dice invece di restare vuoto. */
  configured: boolean
  videos: VideoOut[]
  detail: string | null
  cached: boolean
}

/* Track record: il verdetto su una proposta passata, coi numeri che lo motivano. */
export type OutcomeVerdict = 'hit' | 'flat' | 'miss' | 'na'

export interface OutcomeIdeaOut {
  idea_id: number
  label: string
  verdict: OutcomeVerdict
  promoted_at: string
  horizon_days: number
  pre_velocity: number
  post_velocity: number
  gained: number
  n_new_items: number
  profile: string | null
}

export interface OutcomesOut {
  counts: Record<OutcomeVerdict, number>
  /** Idee con un verdetto vero (na escluse): il denominatore dello hit-rate. */
  judgeable: number
  hit_rate: number | null
  by_profile: Record<string, Record<OutcomeVerdict, number>>
  by_source: Record<string, Record<OutcomeVerdict, number>>
  ideas: OutcomeIdeaOut[]
  /** Proposte in attesa d'orizzonte, e quando matura il primo verdetto. */
  pending: number
  first_due: string | null
}

/* Sviluppo: le idee salvate come piano di lavoro. Tabella dell'utente,
   mai toccata dai run. */
export type WorkspaceStage = 'explore' | 'building' | 'parked'

export interface ChecklistItem {
  text: string
  done: boolean
}

export interface WorkspaceNewItem {
  title: string
  url: string | null
  source: string
  fetched_at: string | null
}

export interface WorkspaceActivity {
  n_new_items: number
  gained_engagement: number
  last_seen: string | null
  /** I 5 arrivi più recenti da quando segui l'idea: titoli, non conteggi. */
  new_items: WorkspaceNewItem[]
}

export interface WorkspaceEntryOut {
  idea_id: number
  label: string
  summary: string | null
  why_text: string | null
  profile: string | null
  stage: WorkspaceStage
  checklist: ChecklistItem[]
  links: string[]
  composite: number
  composite_at_save: number
  created_at: string
  updated_at: string
  activity: WorkspaceActivity
}

export interface WorkspacePatch {
  stage?: WorkspaceStage
  checklist?: ChecklistItem[]
  links?: string[]
}
