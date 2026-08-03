/* Sviluppo: le idee salvate diventano un piano di lavoro.
 *
 * Tre stadi — da esplorare, in sviluppo, parcheggiata — e per ogni idea: le
 * mosse LLM come checklist spuntabile (più i passi aggiunti a mano), i
 * collegamenti ai propri progetti, e l'attività dal radar: cos'è successo DA
 * QUANDO la segui. È il radar al servizio delle idee che hai scelto, non solo
 * di quelle che intercetta. Lo stato qui è dell'utente: i run non lo toccano.
 */

import { useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { Badge, EmptyState, ErrorNotice, IconX, Panel } from '../components/ui'
import { useWorkspace, useWorkspaceActions } from '../hooks/useRadarData'
import type { WorkspaceEntryOut, WorkspaceStage } from '../types'
import { dayMonthYear } from '../dates'

const STAGES: { value: WorkspaceStage; label: string; hint: string }[] = [
  { value: 'explore', label: 'Da esplorare', hint: 'Salvata: ancora da capire.' },
  { value: 'building', label: 'In sviluppo', hint: 'Ci stai lavorando.' },
  { value: 'parked', label: 'Parcheggiata', hint: 'Non ora — non per forza mai.' },
]

function Activity({ entry }: { entry: WorkspaceEntryOut }) {
  const { activity } = entry
  const delta = Math.round((entry.composite - entry.composite_at_save) * 100)
  const quiet = activity.n_new_items === 0 && activity.gained_engagement === 0
  return (
    <div>
      <p className="text-[11px] leading-relaxed text-slate-500">
        Da quando la segui ({dayMonthYear(entry.created_at)}):{' '}
        {quiet ? (
          <span className="text-slate-600">nessun segnale nuovo.</span>
        ) : (
          <>
            <span className="text-phosphor">
              +{activity.n_new_items} item · +
              {Math.round(activity.gained_engagement)} engagement
            </span>
            .
          </>
        )}{' '}
        Punteggio {delta === 0 ? 'fermo' : delta > 0 ? `+${delta}` : `${delta}`}.
      </p>
      {/* I titoli, non solo il conteggio: cosa ha trovato il radar per te. */}
      {activity.new_items.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {activity.new_items.map((item) => (
            <li
              key={`${item.source}-${item.title}`}
              className="flex items-baseline gap-1.5 text-[11px] leading-snug"
            >
              <span aria-hidden className="shrink-0 text-phosphor">
                ▸
              </span>
              {item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="min-w-0 truncate text-slate-300 hover:text-phosphor hover:underline"
                >
                  {item.title}
                </a>
              ) : (
                <span className="min-w-0 truncate text-slate-300">{item.title}</span>
              )}
              <span className="shrink-0 text-slate-600">{item.source}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Checklist({ entry }: { entry: WorkspaceEntryOut }) {
  const { patch, generateMoves } = useWorkspaceActions()
  const [nuovo, setNuovo] = useState('')

  const toggle = (index: number) => {
    const checklist = entry.checklist.map((item, i) =>
      i === index ? { ...item, done: !item.done } : item,
    )
    patch.mutate({ ideaId: entry.idea_id, body: { checklist } })
  }
  const aggiungi = () => {
    const text = nuovo.trim()
    if (!text) return
    patch.mutate({
      ideaId: entry.idea_id,
      body: { checklist: [...entry.checklist, { text, done: false }] },
    })
    setNuovo('')
  }
  const rimuovi = (index: number) => {
    patch.mutate({
      ideaId: entry.idea_id,
      body: { checklist: entry.checklist.filter((_, i) => i !== index) },
    })
  }

  const fatte = entry.checklist.filter((c) => c.done).length
  return (
    <div>
      {entry.checklist.length > 0 ? (
        <p className="hud text-slate-600">
          passi · {fatte}/{entry.checklist.length}
        </p>
      ) : (
        /* Checklist vuota = idea senza mosse (sotto soglia). Sul tavolo se le
           merita: si generano al volo, con l'attesa dichiarata (~10s di LLM). */
        <button
          onClick={() => generateMoves.mutate(entry.idea_id)}
          disabled={generateMoves.isPending}
          className="glass glass-hover rounded-lg px-2.5 py-1.5 text-xs font-medium text-phosphor disabled:opacity-60"
        >
          {generateMoves.isPending
            ? 'Genero le mosse… (qualche secondo)'
            : '✦ Genera le mosse con l\'LLM'}
        </button>
      )}
      {generateMoves.isError && (
        <p className="mt-1.5 text-[11px] text-flare">
          Non ci sono riuscito: Ollama è acceso?
        </p>
      )}
      <ul className="mt-1.5 space-y-1">
        {entry.checklist.map((item, index) => (
          <li key={`${item.text}-${index}`} className="group flex items-start gap-2">
            <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2 text-xs leading-relaxed">
              <input
                type="checkbox"
                checked={item.done}
                onChange={() => toggle(index)}
                className="mt-0.5 size-3.5 shrink-0 accent-[var(--color-phosphor)]"
              />
              <span
                className={
                  item.done ? 'text-slate-600 line-through' : 'text-slate-300'
                }
              >
                {item.text}
              </span>
            </label>
            <button
              onClick={() => rimuovi(index)}
              aria-label={`Rimuovi il passo: ${item.text}`}
              className="mt-0.5 shrink-0 text-slate-700 opacity-0 transition-opacity group-hover:opacity-100 hover:text-flare focus-visible:opacity-100"
            >
              <IconX />
            </button>
          </li>
        ))}
      </ul>
      <input
        value={nuovo}
        onChange={(e) => setNuovo(e.target.value)}
        onKeyDown={(e: KeyboardEvent) => e.key === 'Enter' && aggiungi()}
        onBlur={aggiungi}
        aria-label="Aggiungi un passo"
        placeholder="Aggiungi un passo… (Invio)"
        className="mt-2 w-full rounded-lg border border-transparent bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-200 transition-colors placeholder:text-slate-600 focus:border-phosphor/30 focus:outline-none"
      />
    </div>
  )
}

function Links({ entry }: { entry: WorkspaceEntryOut }) {
  const { patch } = useWorkspaceActions()
  const [nuovo, setNuovo] = useState('')

  const aggiungi = () => {
    const url = nuovo.trim()
    if (!url) return
    if (!/^https?:\/\//.test(url)) return // il backend rifiuterebbe comunque
    patch.mutate({
      ideaId: entry.idea_id,
      body: { links: [...entry.links, url] },
    })
    setNuovo('')
  }
  const rimuovi = (url: string) => {
    patch.mutate({
      ideaId: entry.idea_id,
      body: { links: entry.links.filter((l) => l !== url) },
    })
  }

  return (
    <div>
      {entry.links.length > 0 && (
        <ul className="space-y-1">
          {entry.links.map((url) => (
            <li key={url} className="group flex items-center gap-2 text-xs">
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="truncate text-signal hover:underline"
              >
                {url.replace(/^https?:\/\//, '')}
              </a>
              <button
                onClick={() => rimuovi(url)}
                aria-label={`Rimuovi il collegamento ${url}`}
                className="shrink-0 text-slate-700 opacity-0 transition-opacity group-hover:opacity-100 hover:text-flare focus-visible:opacity-100"
              >
                <IconX />
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        value={nuovo}
        onChange={(e) => setNuovo(e.target.value)}
        onKeyDown={(e: KeyboardEvent) => e.key === 'Enter' && aggiungi()}
        onBlur={aggiungi}
        aria-label="Aggiungi un collegamento"
        placeholder="https://… repo, note, prototipo (Invio)"
        className="mt-2 w-full rounded-lg border border-transparent bg-white/[0.03] px-2.5 py-1.5 text-xs text-slate-200 transition-colors placeholder:text-slate-600 focus:border-phosphor/30 focus:outline-none"
      />
    </div>
  )
}

function EntryCard({ entry }: { entry: WorkspaceEntryOut }) {
  const { patch, remove } = useWorkspaceActions()
  return (
    <Panel className="p-4" data-testid="workspace-entry">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to={`?idea=${entry.idea_id}`}
            className="line-clamp-2 font-display text-sm font-medium tracking-tight text-slate-100 hover:text-phosphor"
          >
            {entry.label}
          </Link>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {entry.profile && (
              <Badge className="bg-signal/10 text-signal ring-signal/25">
                {entry.profile}
              </Badge>
            )}
            <Badge className="bg-white/[0.04] text-slate-400 ring-white/10">
              punteggio {Math.round(entry.composite * 100)}
            </Badge>
          </div>
        </div>
        <button
          onClick={() => remove.mutate(entry.idea_id)}
          title="Togli dal tavolo (l'idea resta nel radar)"
          aria-label={`Togli ${entry.label} da Sviluppo`}
          className="shrink-0 rounded-lg p-1.5 text-slate-600 ring-1 ring-white/[0.06] transition-colors hover:text-flare hover:ring-flare/30"
        >
          <IconX />
        </button>
      </div>

      {/* Il contesto senza aprire il dossier: cosa fa, perché conta. */}
      {(entry.summary || entry.why_text) && (
        <p className="mt-2.5 line-clamp-3 text-xs leading-relaxed text-slate-400">
          {entry.why_text || entry.summary}
        </p>
      )}

      <div className="mt-3">
        <Activity entry={entry} />
      </div>

      <div className="mt-3 space-y-3">
        <Checklist entry={entry} />
        <Links entry={entry} />
      </div>

      {/* Lo stadio: un segmented control, non un drag — tre stati non valgono
          una libreria di drag&drop. */}
      <div className="mt-3 flex rounded-xl bg-white/[0.03] p-0.5">
        {STAGES.map((stage) => (
          <button
            key={stage.value}
            onClick={() =>
              patch.mutate({
                ideaId: entry.idea_id,
                body: { stage: stage.value },
              })
            }
            aria-pressed={entry.stage === stage.value}
            title={stage.hint}
            className={`flex-1 rounded-lg px-2 py-1 text-[11px] font-medium transition-all ${
              entry.stage === stage.value
                ? 'bg-phosphor/15 text-phosphor shadow-[inset_0_0_0_1px_rgba(46,232,162,0.25)]'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {stage.label}
          </button>
        ))}
      </div>
    </Panel>
  )
}

export function SviluppoView() {
  const { data: entries, isPending, isError } = useWorkspace()

  if (isError) {
    return <ErrorNotice>Impossibile caricare il tavolo di lavoro.</ErrorNotice>
  }
  if (isPending) return <EmptyState>Caricamento del tavolo…</EmptyState>

  if (!entries || entries.length === 0) {
    return (
      <EmptyState>
        Il tavolo è vuoto. Apri un'idea dal Radar e premi «Sviluppa»: le sue
        mosse diventano una checklist, e il radar ti dirà cosa le succede da
        quel momento in poi.
      </EmptyState>
    )
  }

  return (
    <div className="grid items-start gap-4 lg:grid-cols-3">
      {STAGES.map((stage) => {
        const inStage = entries.filter((e) => e.stage === stage.value)
        return (
          <section key={stage.value} aria-label={stage.label}>
            <h2 className="hud px-1 text-slate-500">
              {stage.label}
              <span className="ml-1.5 tabular-nums text-slate-600">
                {inStage.length}
              </span>
            </h2>
            <div className="mt-2 grid gap-3">
              {inStage.map((entry) => (
                <EntryCard key={entry.idea_id} entry={entry} />
              ))}
              {inStage.length === 0 && (
                <p className="rounded-xl border border-dashed border-white/[0.06] px-3 py-6 text-center text-xs text-slate-700">
                  {stage.hint}
                </p>
              )}
            </div>
          </section>
        )
      })}
    </div>
  )
}
