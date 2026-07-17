import { useState, type CSSProperties } from 'react'
import { IdeaCard } from '../components/IdeaCard'
import { staggerDelay } from '../components/motion'
import { Badge, EmptyState, IconChevron, ScoreRing } from '../components/ui'
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
        Nessun topic. Il raggruppamento richiede gli embedding: assicurati di aver
        fatto <code className="text-phosphor/80">ollama pull nomic-embed-text</code> e
        lancia un run.
      </EmptyState>
    )
  }

  return (
    <div className="grid gap-3">
      {topics.map((topic, index) => {
        const members = ideas
          .filter((i) => i.topic_id === topic.id)
          .sort((a, b) => b.composite - a.composite)
        const open = openId === topic.id
        const proposedRatio = topic.n_ideas > 0 ? topic.n_proposed / topic.n_ideas : 0
        return (
          <div
            key={topic.id}
            className={`glass stagger overflow-hidden rounded-2xl transition-colors duration-300 ${
              open ? 'border-phosphor/25' : ''
            }`}
            style={staggerDelay(index) as CSSProperties}
          >
            <button
              onClick={() => setOpenId(open ? null : topic.id)}
              className="group flex w-full items-center gap-4 p-4 text-left"
            >
              <ScoreRing value={topic.top_composite} />
              <div className="min-w-0 flex-1">
                <h3 className="truncate font-display font-medium tracking-tight text-slate-100 group-hover:text-white">
                  {topic.label}
                </h3>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge className="bg-white/[0.03] text-slate-400 ring-white/[0.07]">
                    {topic.n_ideas} idee
                  </Badge>
                  <Badge className="bg-white/[0.03] text-slate-400 ring-white/[0.07]">
                    {topic.n_items} segnali
                  </Badge>
                  {topic.n_proposed > 0 && (
                    <Badge className="bg-phosphor/10 text-phosphor ring-phosphor/30">
                      {topic.n_proposed} sopra soglia
                    </Badge>
                  )}
                  <span className="text-xs text-slate-600">
                    media {Math.round(topic.avg_composite * 100)}
                  </span>
                </div>
                {/* quota di idee sopra soglia nel topic */}
                <div className="mt-2.5 h-1 w-full max-w-56 overflow-hidden rounded-full bg-white/[0.05]">
                  <div
                    className="grow-bar h-full rounded-full bg-gradient-to-r from-phosphor/40 to-phosphor"
                    style={{
                      width: `${Math.max(proposedRatio * 100, topic.n_proposed > 0 ? 8 : 0)}%`,
                      animationDelay: `${index * 60}ms`,
                    }}
                  />
                </div>
              </div>
              <IconChevron
                open={open}
                className="shrink-0 text-slate-600 group-hover:text-phosphor/70"
              />
            </button>
            {/* fisarmonica fluida senza misurare le altezze */}
            <div
              className="grid transition-[grid-template-rows] duration-400 ease-out"
              style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
            >
              <div className="overflow-hidden">
                <div className="grid gap-2 border-t border-white/[0.06] p-3">
                  {members.map((idea, memberIndex) => (
                    <IdeaCard
                      key={idea.id}
                      idea={idea}
                      index={memberIndex}
                      onSelect={onSelect}
                    />
                  ))}
                  {members.length === 0 && (
                    <p className="p-3 text-sm text-slate-500">
                      Le idee di questo topic non sono nella vista corrente.
                    </p>
                  )}
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
