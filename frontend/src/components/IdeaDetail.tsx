import { useEffect, useState } from 'react'
import { api } from '../api'
import type { IdeaDetailOut } from '../types'
import {
  Badge,
  DIFFICULTY_STYLES,
  MetricBar,
  ScoreRing,
  Sparkline,
  STATUS_STYLES,
} from './ui'

const METRIC_HINTS: Record<string, string> = {
  Heat: 'Velocità di crescita (stelle/giorno, engagement), non popolarità assoluta.',
  Credibility: 'Affidabilità della fonte e presenza di un autore identificabile.',
  Feasibility: 'Quanto è realizzabile da un team di 1-3 persone (stima LLM).',
  Opportunity: 'Recente E non ancora saturo: un mercato già chiuso vale poco.',
  Fit: 'Aderenza alle tue keyword. Moltiplica il punteggio: fuori tema = abbattuta.',
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('it-IT', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

export function IdeaDetail({
  ideaId,
  onClose,
}: {
  ideaId: number
  onClose: () => void
}) {
  const [idea, setIdea] = useState<IdeaDetailOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setIdea(null)
    setError(null)
    api
      .idea(ideaId)
      .then(setIdea)
      .catch(() => setError('Impossibile caricare il dettaglio.'))
  }, [ideaId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-slate-950/70 backdrop-blur-sm"
        onClick={onClose}
      />
      <aside className="relative flex h-full w-full max-w-xl flex-col overflow-y-auto border-l border-slate-800 bg-slate-950 shadow-2xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-800 bg-slate-950/95 p-5 backdrop-blur">
          <div className="min-w-0">
            {idea ? (
              <>
                <h2 className="text-lg font-semibold text-slate-100">{idea.label}</h2>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Badge className={STATUS_STYLES[idea.status] ?? STATUS_STYLES.processed}>
                    {idea.status}
                  </Badge>
                  {idea.difficulty && (
                    <Badge className={DIFFICULTY_STYLES[idea.difficulty]}>
                      difficoltà: {idea.difficulty}
                    </Badge>
                  )}
                  {idea.topic_label && (
                    <Badge className="bg-sky-500/10 text-sky-300 ring-sky-500/30">
                      {idea.topic_label}
                    </Badge>
                  )}
                </div>
              </>
            ) : (
              <h2 className="text-lg font-semibold text-slate-500">Caricamento…</h2>
            )}
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
            aria-label="Chiudi"
          >
            ✕
          </button>
        </header>

        {error && <p className="p-5 text-sm text-rose-400">{error}</p>}

        {idea && (
          <div className="space-y-6 p-5">
            <section className="flex items-start gap-4">
              <ScoreRing value={idea.composite} size={64} />
              <div className="min-w-0 flex-1">
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Perché
                </h3>
                <p className="mt-1 text-sm leading-relaxed text-slate-300">
                  {idea.why_text || 'Nessuna motivazione generata.'}
                </p>
              </div>
            </section>

            {idea.summary && (
              <section>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Sintesi
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-400">
                  {idea.summary}
                </p>
              </section>
            )}

            <section>
              <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                KPI
              </h3>
              <div className="mt-2.5 grid gap-2">
                <MetricBar label="Heat" value={idea.heat} hint={METRIC_HINTS.Heat} />
                <MetricBar
                  label="Credibility"
                  value={idea.credibility}
                  hint={METRIC_HINTS.Credibility}
                />
                <MetricBar
                  label="Feasibility"
                  value={idea.feasibility}
                  hint={METRIC_HINTS.Feasibility}
                />
                <MetricBar
                  label="Opportunity"
                  value={idea.opportunity}
                  hint={METRIC_HINTS.Opportunity}
                />
                <MetricBar label="Fit" value={idea.fit} hint={METRIC_HINTS.Fit} />
              </div>
            </section>

            {idea.history.length > 1 && (
              <section>
                <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                  Andamento del punteggio
                </h3>
                <div className="mt-2 flex items-center gap-3 text-sky-400">
                  <Sparkline values={idea.history.map((h) => h.composite)} />
                  <span className="text-xs text-slate-500">
                    su {idea.history.length} run
                  </span>
                </div>
              </section>
            )}

            <section>
              <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Segnali ({idea.items.length})
              </h3>
              <ul className="mt-2.5 space-y-2">
                {idea.items.map((item, i) => (
                  <li
                    key={i}
                    className="rounded-xl border border-slate-800/80 bg-slate-900/40 p-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm text-slate-200">{item.title}</p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          {item.source}
                          {item.author && ` · ${item.author}`}
                          {item.created_at && ` · ${formatDate(item.created_at)}`}
                        </p>
                      </div>
                      {item.url && (
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                          className="shrink-0 rounded-lg bg-slate-800 px-2.5 py-1 text-xs text-sky-300 hover:bg-slate-700"
                        >
                          Apri ↗
                        </a>
                      )}
                    </div>
                    {item.engagement && Object.keys(item.engagement).length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {Object.entries(item.engagement).map(([k, v]) => (
                          <Badge
                            key={k}
                            className="bg-slate-800/60 text-slate-400 ring-slate-700/50"
                          >
                            {k}: {v}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>

            <footer className="border-t border-slate-800 pt-4 text-xs text-slate-600">
              Vista la prima volta il {formatDate(idea.first_seen)} · ultima volta il{' '}
              {formatDate(idea.last_seen)}
            </footer>
          </div>
        )}
      </aside>
    </div>
  )
}
