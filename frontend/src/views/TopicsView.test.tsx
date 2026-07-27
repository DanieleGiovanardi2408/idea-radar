/* La vista Topic a due livelli: macro dai profili, micro dal clustering.
 *
 * Prima era una lista piatta di 998 voci. Il macro non va inventato — esiste già
 * nei profili dichiarati in config.yaml — quindi qui si verifica solo che la
 * gerarchia venga mostrata, non che venga calcolata. */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { TopicsView } from './TopicsView'
import { fakeIdeaOut, mockFetch, renderWithProviders } from '../test/utils'
import type { ProfileOut, TopicOut } from '../types'

function topic(overrides: Partial<TopicOut>): TopicOut {
  return {
    id: 1,
    label: 'Agenti AI per il codice',
    profile: 'ai-agents',
    n_ideas: 4,
    n_items: 6,
    n_proposed: 1,
    avg_composite: 0.3,
    top_composite: 0.5,
    first_seen: '2026-07-01T00:00:00',
    last_seen: '2026-07-26T00:00:00',
    ...overrides,
  }
}

const PROFILES: ProfileOut[] = [
  {
    name: 'ai-agents',
    label: 'Agenti AI',
    keywords: ['ai agents'],
    n_ideas: 186,
    n_ungrouped: 0,
  },
  {
    name: 'domotica',
    label: 'Domotica e IoT',
    keywords: ['esp32'],
    n_ideas: 9,
    n_ungrouped: 0,
  },
]

function show(topics: TopicOut[], profiles = PROFILES) {
  const mock = mockFetch({
    '/profiles': profiles,
    '/topics': topics,
    '/ideas': [fakeIdeaOut({ id: 42, label: 'Un agente che scrive test' })],
    '/stats': { n_items: 0, n_ideas: 0, n_topics: 0, n_proposed: 0, n_runs: 0, items_by_source: {}, recent_runs: [] },
  })
  renderWithProviders(<TopicsView onSelect={() => {}} />)
  return mock
}

describe('TopicsView', () => {
  it('raggruppa i temi sotto il loro macro-tema', async () => {
    show([
      topic({ id: 1, label: 'Agenti per il codice', profile: 'ai-agents' }),
      topic({ id: 2, label: 'Home Assistant', profile: 'domotica' }),
    ])

    expect(await screen.findByText('Agenti AI')).toBeInTheDocument()
    expect(screen.getByText('Domotica e IoT')).toBeInTheDocument()
    expect(screen.getByText('Agenti per il codice')).toBeInTheDocument()
    expect(screen.getByText('Home Assistant')).toBeInTheDocument()
  })

  it('mette i temi senza profilo in un gruppo a parte, non li nasconde', async () => {
    show([
      topic({ id: 1, label: 'Con tema', profile: 'ai-agents' }),
      topic({ id: 2, label: 'Orfano', profile: null }),
    ])

    expect(await screen.findByText('Senza tema')).toBeInTheDocument()
    expect(screen.getByText('Orfano')).toBeInTheDocument()
  })

  it('un profilo senza temi non compare come intestazione vuota', async () => {
    show([topic({ id: 1, profile: 'ai-agents' })])

    await screen.findByText('Agenti AI')
    expect(screen.queryByText('Domotica e IoT')).not.toBeInTheDocument()
  })

  it('conta temi e idee di ogni macro-tema', async () => {
    show([
      topic({ id: 1, profile: 'ai-agents', n_ideas: 4 }),
      topic({ id: 2, profile: 'ai-agents', n_ideas: 3 }),
    ])

    expect(await screen.findByText('2 temi · 7 idee')).toBeInTheDocument()
  })

  it('chiede le idee al server solo quando apri un tema', async () => {
    const user = userEvent.setup()
    // Regressione: `/ideas` è paginato a 100, quindi filtrare la lista globale
    // mostrava "non sono nella vista corrente" per quasi tutti i temi.
    const { calls } = show([topic({ id: 7, label: 'Da aprire' })])

    await screen.findByText('Da aprire')
    expect(calls.some((c) => c.url.includes('topic_id=7'))).toBe(false)

    await user.click(screen.getByRole('button', { name: /da aprire/i }))

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('topic_id=7'))).toBe(true),
    )
    expect(await screen.findByText('Un agente che scrive test')).toBeInTheDocument()
  })

  it('il filtro sui singleton chiede min_ideas al server', async () => {
    const user = userEvent.setup()
    const { calls } = show([topic({})])

    await screen.findByText(/solo temi con più idee/i)
    expect(calls.some((c) => c.url.includes('min_ideas=2'))).toBe(true)

    await user.click(screen.getByRole('button', { name: /solo temi con più idee/i }))

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('min_ideas=1'))).toBe(true),
    )
  })
})

describe('idee non raggruppate', () => {
  it('il macro-tema dice quante sono, e la sezione si apre', async () => {
    const user = userEvent.setup()
    const { calls } = show(
      [topic({ id: 1, label: 'Agenti per il codice', profile: 'ai-agents', n_ideas: 4 })],
      [
        {
          name: 'ai-agents',
          label: 'Agenti AI',
          keywords: ['ai agents'],
          n_ideas: 186,
          n_ungrouped: 120,
        },
      ],
    )

    expect(await screen.findByText(/120 non raggruppate/)).toBeInTheDocument()

    // Chiusa non chiede niente al server: sono centinaia di idee.
    expect(calls.some((c) => c.url.includes('ungrouped=true'))).toBe(false)

    await user.click(
      screen.getByRole('button', { name: /non raggruppate in agenti ai/i }),
    )

    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url.includes('ungrouped=true') && c.url.includes('profile=ai-agents'),
        ),
      ).toBe(true),
    )
    expect(await screen.findByText('Un agente che scrive test')).toBeInTheDocument()
  })

  it('un tema con SOLE idee non raggruppate non sparisce dalla vista', async () => {
    // Prima un profilo senza topic veri non compariva affatto: significava
    // nascondere la parte più grossa dell'archivio.
    show(
      [],
      [
        {
          name: 'domotica',
          label: 'Domotica e IoT',
          keywords: ['esp32'],
          n_ideas: 9,
          n_ungrouped: 9,
        },
      ],
    )

    expect(await screen.findByText('Domotica e IoT')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /non raggruppate in domotica e iot/i }),
    ).toBeInTheDocument()
  })
})
