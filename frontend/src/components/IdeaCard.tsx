import type { IdeaOut } from '../types'
import { Badge, DIFFICULTY_STYLES, ScoreRing, STATUS_STYLES } from './ui'

export function IdeaCard({
  idea,
  onSelect,
}: {
  idea: IdeaOut
  onSelect: (id: number) => void
}) {
  const sources = Array.from(new Set(idea.items.map((i) => i.source)))
  return (
    <button
      onClick={() => onSelect(idea.id)}
      className="group w-full rounded-2xl border border-slate-800/80 bg-slate-900/40 p-4 text-left transition hover:border-slate-700 hover:bg-slate-900/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
    >
      <div className="flex items-start gap-4">
        <ScoreRing value={idea.composite} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-medium text-slate-100 group-hover:text-white">
            {idea.label}
          </h3>
          <p className="mt-1 line-clamp-2 text-sm text-slate-400">
            {idea.why_text || idea.summary || 'Nessuna descrizione.'}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            <Badge className={STATUS_STYLES[idea.status] ?? STATUS_STYLES.processed}>
              {idea.status}
            </Badge>
            {idea.difficulty && (
              <Badge className={DIFFICULTY_STYLES[idea.difficulty]}>
                {idea.difficulty}
              </Badge>
            )}
            {idea.topic_label && (
              <Badge className="bg-sky-500/10 text-sky-300 ring-sky-500/30">
                {idea.topic_label}
              </Badge>
            )}
            {sources.map((s) => (
              <Badge key={s} className="bg-slate-800/60 text-slate-400 ring-slate-700/50">
                {s}
              </Badge>
            ))}
            {idea.n_items > 1 && (
              <Badge className="bg-slate-800/60 text-slate-400 ring-slate-700/50">
                {idea.n_items} segnali
              </Badge>
            )}
          </div>
        </div>
      </div>
    </button>
  )
}
