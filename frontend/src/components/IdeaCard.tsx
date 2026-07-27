import type { CSSProperties } from 'react'
import { usePatchIdea } from '../hooks/useRadarData'
import type { IdeaOut } from '../types'
import { staggerDelay } from './motion'
import {
  Badge,
  DIFFICULTY_STYLES,
  IconArrowUpRight,
  IconDismiss,
  IconPin,
  IconRestore,
  ScoreRing,
  STATUS_STYLES,
} from './ui'

export function IdeaCard({
  idea,
  index = 0,
  onSelect,
}: {
  idea: IdeaOut
  index?: number
  onSelect: (id: number) => void
}) {
  // Nessun `disabled` sui pulsanti: l'aggiornamento è ottimistico, quindi
  // l'icona cambia subito e torna indietro se il server rifiuta.
  const { mutate: patchIdea } = usePatchIdea()
  const sources = Array.from(new Set(idea.items.map((i) => i.source)))
  const rank = index + 1
  const dismissed = idea.dismissed_at !== null

  return (
    /* Non è un <button>: dentro ci sono i bottoni azione (nested button è
       HTML invalido), quindi la card è un div con semantica da bottone. */
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(idea.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(idea.id)
        }
      }}
      className="glass glass-hover stagger group relative w-full cursor-pointer overflow-hidden rounded-2xl p-4 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-phosphor/60"
      style={staggerDelay(index) as CSSProperties}
      data-testid="idea-card"
    >
      {/* numerale di rotta, da HUD */}
      <span className="hud pointer-events-none absolute top-3 right-4 text-[22px] font-semibold text-white/[0.05] transition-colors duration-300 group-hover:text-phosphor/15">
        {String(rank).padStart(2, '0')}
      </span>

      <div className="flex items-start gap-4">
        <ScoreRing value={idea.composite} />
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-1.5 pr-8 font-display font-medium tracking-tight text-slate-100 transition-colors group-hover:text-white">
            <span className="truncate">{idea.label}</span>
            <IconArrowUpRight className="shrink-0 -translate-x-1 text-phosphor opacity-0 transition-all duration-300 group-hover:translate-x-0 group-hover:opacity-100" />
          </h3>
          <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-slate-400">
            {idea.why_text || idea.summary || 'Nessuna descrizione.'}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            {idea.pinned && (
              <Badge className="bg-phosphor/10 text-phosphor ring-phosphor/30">
                <IconPin filled /> pinnata
              </Badge>
            )}
            {dismissed && (
              <Badge className="bg-flare/10 text-flare ring-flare/30">scartata</Badge>
            )}
            <Badge className={STATUS_STYLES[idea.status] ?? STATUS_STYLES.processed}>
              {idea.status}
            </Badge>
            {idea.difficulty && (
              <Badge className={DIFFICULTY_STYLES[idea.difficulty]}>
                {idea.difficulty}
              </Badge>
            )}
            {idea.topic_label && (
              <Badge className="bg-white/[0.04] text-slate-300 ring-white/10">
                {idea.topic_label}
              </Badge>
            )}
            {sources.map((s) => (
              <Badge key={s} className="bg-white/[0.03] text-slate-500 ring-white/[0.07]">
                {s}
              </Badge>
            ))}
            {idea.n_items > 1 && (
              <Badge className="bg-white/[0.03] text-slate-500 ring-white/[0.07]">
                {idea.n_items} segnali
              </Badge>
            )}

            {/* azioni rapide: fermano la propagazione (la card apre il dettaglio) */}
            <span className="ml-auto flex items-center gap-1">
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  patchIdea({ id: idea.id, body: { pinned: !idea.pinned } })
                }}
                title={idea.pinned ? 'Togli il pin' : 'Pinna in cima'}
                aria-label={idea.pinned ? 'Togli il pin' : 'Pinna in cima'}
                className={`rounded-lg p-1.5 ring-1 transition-colors duration-300 ${
                  idea.pinned
                    ? 'bg-phosphor/10 text-phosphor ring-phosphor/30'
                    : 'text-slate-600 ring-white/[0.06] hover:text-phosphor hover:ring-phosphor/30'
                }`}
              >
                <IconPin filled={idea.pinned} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  patchIdea({ id: idea.id, body: { dismissed: !dismissed } })
                }}
                title={dismissed ? 'Ripristina' : 'Scarta'}
                aria-label={dismissed ? 'Ripristina' : 'Scarta'}
                className={`rounded-lg p-1.5 ring-1 transition-colors duration-300 ${
                  dismissed
                    ? 'text-slate-500 ring-white/[0.06] hover:text-phosphor hover:ring-phosphor/30'
                    : 'text-slate-600 ring-white/[0.06] hover:text-flare hover:ring-flare/30'
                }`}
              >
                {dismissed ? <IconRestore /> : <IconDismiss />}
              </button>
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
