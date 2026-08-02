/* Il track record: il radar controlla le proprie previsioni.
 *
 * Per ogni idea proposta da abbastanza tempo il backend guarda cos'è successo
 * DOPO la promozione (engagement misurato, item nuovi) ed emette un verdetto.
 * Questo pannello lo mostra senza addolcirlo: un radar che non dice quante ne
 * azzecca è un oroscopo. L'hit-rate esclude le "na" (idee senza contatori
 * vivi), che punirebbero le fonti sbagliate e non le previsioni. */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Panel } from './ui'
import { useOutcomes } from '../hooks/useRadarData'
import type { OutcomeIdeaOut, OutcomeVerdict } from '../types'
import { dayMonthYear } from '../dates'

const VERDICT_STYLES: Record<OutcomeVerdict, string> = {
  hit: 'bg-phosphor/10 text-phosphor ring-phosphor/30',
  flat: 'bg-signal/10 text-signal ring-signal/30',
  miss: 'bg-flare/10 text-flare ring-flare/30',
  na: 'bg-white/[0.04] text-slate-500 ring-white/10',
}

const VERDICT_LABELS: Record<OutcomeVerdict, string> = {
  hit: 'hit',
  flat: 'flat',
  miss: 'miss',
  na: 'n.g.',
}

const VERDICT_HINTS: Record<OutcomeVerdict, string> = {
  hit: 'Ha continuato a crescere dopo la proposta: il radar aveva ragione.',
  flat: 'Viva ma ferma: né esplosa né morta.',
  miss: 'Morta lì: nessuna crescita, nessun segnale nuovo.',
  na: 'Non giudicabile: nessun item su fonti con contatori vivi.',
}

function VerdictBadge({ verdict }: { verdict: OutcomeVerdict }) {
  return (
    <span
      title={VERDICT_HINTS[verdict]}
      className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${VERDICT_STYLES[verdict]}`}
    >
      {VERDICT_LABELS[verdict]}
    </span>
  )
}

function JudgedIdea({ idea }: { idea: OutcomeIdeaOut }) {
  return (
    <li>
      <Link
        to={`?idea=${idea.idea_id}`}
        className="flex items-center justify-between gap-3 rounded-lg px-2 py-2 text-xs transition-colors hover:bg-white/[0.03]"
      >
        <span className="flex min-w-0 items-center gap-2">
          <VerdictBadge verdict={idea.verdict} />
          <span className="truncate text-slate-300">{idea.label}</span>
        </span>
        <span className="shrink-0 tabular-nums text-slate-600">
          {idea.verdict !== 'na' && (
            <>
              +{Math.round(idea.gained)} eng
              {idea.n_new_items > 0 && ` · ${idea.n_new_items} nuovi`}
              {' · '}
            </>
          )}
          proposta {dayMonthYear(idea.promoted_at)}
        </span>
      </Link>
    </li>
  )
}

const PAGE = 8

export function TrackRecord() {
  const { data, isError } = useOutcomes()
  const [shown, setShown] = useState(PAGE)

  if (isError) {
    return (
      <Panel className="p-5">
        <h3 className="hud text-slate-500">Track record</h3>
        <p className="mt-3 text-xs text-flare">Impossibile caricare i verdetti.</p>
      </Panel>
    )
  }
  if (!data) return null

  const { counts, judgeable, hit_rate, ideas, pending, first_due } = data
  const total = ideas.length

  return (
    <Panel className="p-5" data-testid="track-record">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="hud text-slate-500">Track record</h3>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
            Ogni proposta viene giudicata a orizzonte compiuto: com'è andata
            davvero, misurata sui contatori delle fonti.
          </p>
        </div>
        {hit_rate !== null && (
          <div className="shrink-0 text-right">
            <div className="font-display text-3xl font-semibold tabular-nums text-phosphor">
              {Math.round(hit_rate * 100)}%
            </div>
            <div className="text-[10px] text-slate-600">
              hit-rate su {judgeable} giudicabili
            </div>
          </div>
        )}
      </div>

      {total === 0 ? (
        <p className="mt-4 text-xs leading-relaxed text-slate-600">
          {pending > 0 && first_due ? (
            <>
              <span className="text-slate-400">{pending} proposte</span> in
              attesa d'orizzonte: il primo verdetto matura il{' '}
              <span className="text-phosphor">{dayMonthYear(first_due)}</span>,
              poi arriveranno giorno per giorno. Il giudice non anticipa.
            </>
          ) : (
            <>
              Nessun verdetto ancora: una proposta si giudica quando il suo
              orizzonte è compiuto. I verdetti si calcolano in coda a ogni run
              (o subito con{' '}
              <code className="text-slate-400">idea-radar outcomes</code>).
            </>
          )}
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-1.5">
            {(Object.keys(VERDICT_LABELS) as OutcomeVerdict[]).map((verdict) => (
              <span
                key={verdict}
                title={VERDICT_HINTS[verdict]}
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ${VERDICT_STYLES[verdict]}`}
              >
                {VERDICT_LABELS[verdict]}{' '}
                <span className="tabular-nums opacity-80">
                  {counts[verdict] ?? 0}
                </span>
              </span>
            ))}
          </div>

          <ul className="mt-3 divide-y divide-white/[0.04]">
            {ideas.slice(0, shown).map((idea) => (
              <JudgedIdea key={idea.idea_id} idea={idea} />
            ))}
          </ul>
          {total > shown && (
            <button
              onClick={() => setShown((n) => n + PAGE)}
              className="mt-2 w-full rounded-lg px-2 py-1.5 text-xs text-slate-500 transition-colors hover:bg-white/[0.03] hover:text-phosphor"
            >
              Mostra altre ({shown} di {total})
            </button>
          )}
        </>
      )}
    </Panel>
  )
}
