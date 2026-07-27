/* La porta d'ingresso dell'app.
 *
 * Il layout a tre colonne del Radar si attivava anche su "/", ma la colonna
 * centrale conteneva un <Routes> che dichiarava solo "/radar": sull'indirizzo
 * nudo nessuna route corrispondeva e la sala controllo appariva con i due
 * pannelli laterali e un buco al centro — niente quadrante, niente lista, tab
 * non evidenziato. Il bug è vissuto per giorni perché a occhio si arrivava
 * sempre da un link a /radar, ed è finito in uno screenshot del README.
 * Questi test guardano "/" apposta. */

import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'
import { fakeIdeaOut, mockFetch, renderWithProviders } from './test/utils'

const VUOTO = {
  '/stats': { last_run: null, n_runs: 3 },
  // Due profili: il selettore dei temi compare solo se c'è una scelta da fare.
  '/profiles': [
    { name: 'ai-agents', label: 'Agenti AI', keywords: ['agent'], n_ideas: 1 },
    { name: 'llm-apps', label: 'App LLM', keywords: ['llm'], n_ideas: 0 },
  ],
  '/topics': [],
  '/trends': [],
  '/runs': [],
  '/rhythm': {
    days: 28,
    n_items: 0,
    n_without_date: 0,
    grid: Array.from({ length: 7 }, () => Array(24).fill(0)),
    peak: 0,
    by_source: {},
  },
  '/videos': { items: [], fetched_at: null, quota_exhausted: false },
}

function apri(route: string) {
  mockFetch({
    ...VUOTO,
    '/ideas': [fakeIdeaOut({ id: 1, label: 'Un runtime per agenti locali' })],
  })
  return renderWithProviders(<App />, undefined, route)
}

describe('indirizzo di ingresso', () => {
  it('su "/" mostra il radar, non una sala controllo vuota', async () => {
    apri('/')
    // La colonna centrale: il filtro dei temi e almeno un'idea.
    expect(await screen.findByText('Tema')).toBeInTheDocument()
    expect(await screen.findByTestId('idea-card')).toBeInTheDocument()
  })

  it('su "/" il tab Radar risulta attivo', async () => {
    apri('/')
    // aria-current arriva da NavLink: se l'indirizzo resta "/" nessun tab lo ha,
    // e l'indicatore scorrevole (indicizzato per pathname) resta a zero.
    const radar = await screen.findByRole('link', { name: /Radar/ })
    expect(radar).toHaveAttribute('aria-current', 'page')
  })

  it('su "/radar" mostra le stesse cose', async () => {
    apri('/radar')
    expect(await screen.findByText('Tema')).toBeInTheDocument()
    expect(await screen.findByTestId('idea-card')).toBeInTheDocument()
  })
})
