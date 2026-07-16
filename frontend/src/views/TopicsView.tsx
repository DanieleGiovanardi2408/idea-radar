import { useState } from 'react'
import { IdeaCard } from '../components/IdeaCard'
import { Badge, EmptyState, Panel, ScoreRing } from '../components/ui'
import type { IdeaOut, TopicOut } from '../types'

export function TopicsView({
  topics,
  ideas,
  onSelect,
}: {
  topics: TopicOut[]
  ideas: IdeaOut[]
  onSelect: (id: number) => void
}) {
  const [openId, setOpenId] = useState<number | null>(null)

  if (topics.length === 0) {
    return (
      <EmptyState>
        Nessun topic. Il raggruppamento richiede gli embedding: assicurati di aver fatto{' '}
        <code className="text-slate-400">ollama pull nomic-embed-text</code> e lancia un
        run.
      </EmptyState>
    )
  }

  return (
    <div className="grid gap-3">
      {topics.map((topic) => {
        const members = ideas
          .filter((i) => i.topic_id === topic.id)
          .sort((a, b) => b.composite - a.composite)
        const open = openId === topic.id
        return (
          <Panel key={topic.id}>
            <button
              onClick={() => setOpenId(open ? null : topic.id)}
              className="flex w-full items-center gap-4 p-4 text-left"
            >
              <ScoreRing value={topic.top_composite} />
              <div className="min-w-0 flex-1">
                <h3 className="truncate font-medium text-slate-100">{topic.label}</h3>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge className="bg-slate-800/60 text-slate-400 ring-slate-700/50">
                    {topic.n_ideas} idee
                  </Badge>
                  <Badge className="bg-slate-800/60 text-slate-400 ring-slate-700/50">
                    {topic.n_items} segnali
                  </Badge>
                  {topic.n_proposed > 0 && (
                    <Badge className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">
                      {topic.n_proposed} sopra soglia
                    </Badge>
                  )}
                  <span className="text-xs text-slate-600">
                    media {Math.round(topic.avg_composite * 100)}
                  </span>
                </div>
              </div>
              <span className="shrink-0 text-slate-600">{open ? '▲' : '▼'}</span>
            </button>
            {open && (
              <div className="grid gap-2 border-t border-slate-800/80 p-3">
                {members.map((idea) => (
                  <IdeaCard key={idea.id} idea={idea} onSelect={onSelect} />
                ))}
              </div>
            )}
          </Panel>
        )
      })}
    </div>
  )
}
