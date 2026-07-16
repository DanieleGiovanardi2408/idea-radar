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
  why_text: string | null
  difficulty: string | null
  n_items: number
  first_seen: string | null
  last_seen: string | null
  items: ItemOut[]
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
  sources: Record<string, { fetched: number; new: number; error?: string }> | null
}

export type StatsOut = {
  n_items: number
  n_ideas: number
  n_topics: number
  n_proposed: number
  n_runs: number
  items_by_source: Record<string, number>
  last_run: RunOut | null
  recent_runs: RunOut[]
}
