/* Il pannello che dice quante ne azzecca il radar: hit-rate, contatori per
 * verdetto e la lista delle idee giudicate, con deep-link al dossier. */

import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrackRecord } from './TrackRecord'
import { mockFetch, renderWithProviders } from '../test/utils'
import type { OutcomeIdeaOut, OutcomesOut } from '../types'

afterEach(() => {
  vi.unstubAllGlobals()
})

function giudicata(overrides: Partial<OutcomeIdeaOut> = {}): OutcomeIdeaOut {
  return {
    idea_id: 1,
    label: 'Runtime per agenti locali',
    verdict: 'hit',
    promoted_at: '2026-06-20T10:00:00',
    horizon_days: 30,
    pre_velocity: 8,
    post_velocity: 6,
    gained: 240,
    n_new_items: 3,
    profile: 'ai-agents',
    ...overrides,
  }
}

function trackRecord(overrides: Partial<OutcomesOut> = {}): OutcomesOut {
  return {
    counts: { hit: 2, flat: 1, miss: 1, na: 1 },
    judgeable: 4,
    hit_rate: 0.5,
    by_profile: {},
    by_source: {},
    ideas: [
      giudicata(),
      giudicata({ idea_id: 2, label: 'Sensore LoRa per serre', verdict: 'miss', gained: 0 }),
    ],
    pending: 0,
    first_due: null,
    ...overrides,
  }
}

describe('TrackRecord', () => {
  it('mostra hit-rate e contatori per verdetto', async () => {
    mockFetch({ '/outcomes': trackRecord() })
    renderWithProviders(<TrackRecord />)

    expect(await screen.findByText('50%')).toBeInTheDocument()
    expect(screen.getByText('hit-rate su 4 giudicabili')).toBeInTheDocument()
    // I contatori: hit 2 (chip) più il badge "hit" della riga — basta che il chip ci sia.
    expect(screen.getAllByText('hit').length).toBeGreaterThan(0)
  })

  it('le idee giudicate portano al dossier via deep-link', async () => {
    mockFetch({ '/outcomes': trackRecord() })
    renderWithProviders(<TrackRecord />)

    const link = await screen.findByRole('link', {
      name: /Runtime per agenti locali/,
    })
    expect(link).toHaveAttribute('href', expect.stringContaining('?idea=1'))
  })

  it('senza verdetti spiega quando arriveranno, non un pannello vuoto', async () => {
    mockFetch({
      '/outcomes': trackRecord({
        counts: { hit: 0, flat: 0, miss: 0, na: 0 },
        judgeable: 0,
        hit_rate: null,
        ideas: [],
      }),
    })
    renderWithProviders(<TrackRecord />)

    expect(
      await screen.findByText(/Nessun verdetto ancora/),
    ).toBeInTheDocument()
  })

  it('con proposte in attesa mostra il conto alla rovescia', async () => {
    mockFetch({
      '/outcomes': trackRecord({
        counts: { hit: 0, flat: 0, miss: 0, na: 0 },
        judgeable: 0,
        hit_rate: null,
        ideas: [],
        pending: 436,
        first_due: '2026-08-09T15:46:08',
      }),
    })
    renderWithProviders(<TrackRecord />)

    expect(await screen.findByText(/436 proposte/)).toBeInTheDocument()
    expect(screen.getByText(/9 ago 2026|09\/08|9 agosto/i)).toBeInTheDocument()
  })

  it('"Mostra altre" espande la lista oltre le prime 8', async () => {
    const tante = Array.from({ length: 12 }, (_, i) =>
      giudicata({ idea_id: i + 1, label: `Idea ${i + 1}` }),
    )
    mockFetch({ '/outcomes': trackRecord({ ideas: tante }) })
    renderWithProviders(<TrackRecord />)

    expect(await screen.findByText('Idea 1')).toBeInTheDocument()
    expect(screen.queryByText('Idea 9')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /Mostra altre/ }))
    expect(screen.getByText('Idea 9')).toBeInTheDocument()
  })
})
