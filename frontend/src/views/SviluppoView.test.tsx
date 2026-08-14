/* Il tavolo di lavoro: tre stadi, checklist spuntabile, collegamenti,
 * attività dal radar. Lo stato è dell'utente: ogni azione è una PATCH
 * esplicita, verificata qui sul contratto di rete. */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SviluppoView } from './SviluppoView'
import { mockFetch, renderWithProviders } from '../test/utils'
import type { WorkspaceEntryOut } from '../types'

afterEach(() => {
  vi.unstubAllGlobals()
})

function entry(overrides: Partial<WorkspaceEntryOut> = {}): WorkspaceEntryOut {
  return {
    idea_id: 1,
    label: 'Runtime per agenti locali',
    summary: 'Fa girare agenti in locale.',
    why_text: 'Risolve il deployment locale degli agenti.',
    profile: 'ai-agents',
    stage: 'explore',
    checklist: [
      { text: 'Scrivi il prototipo', done: false },
      { text: 'Compra il dominio', done: true },
    ],
    links: ['https://github.com/me/proto'],
    composite: 0.61,
    composite_at_save: 0.55,
    created_at: '2026-07-20T10:00:00',
    updated_at: '2026-08-01T10:00:00',
    activity: {
      n_new_items: 2,
      gained_engagement: 140,
      last_seen: '2026-08-02T09:00:00',
      new_items: [
        {
          title: 'Show HN: agent runtime v2',
          url: 'https://news.ycombinator.com/item?id=1',
          source: 'hn',
          fetched_at: '2026-08-01T09:00:00',
        },
      ],
    },
    ...overrides,
  }
}

function monta(entries: WorkspaceEntryOut[] = [entry()]) {
  return mockFetch({
    '/workspace': (init?: RequestInit) =>
      init?.method && init.method !== 'GET' ? entries[0] : entries,
    '/stats': { last_run: null, n_runs: 1 },
  })
}

describe('SviluppoView', () => {
  it('raggruppa per stadio e mostra l\'attività dal radar', async () => {
    monta([
      entry(),
      entry({ idea_id: 2, label: 'Sensore LoRa', stage: 'building' }),
    ])
    renderWithProviders(<SviluppoView />)

    expect(await screen.findByText('Runtime per agenti locali')).toBeInTheDocument()
    // Colonne: la card giusta sotto lo stadio giusto.
    expect(
      screen.getByRole('region', { name: 'Da esplorare' }),
    ).toHaveTextContent('Runtime per agenti locali')
    expect(
      screen.getByRole('region', { name: 'In sviluppo' }),
    ).toHaveTextContent('Sensore LoRa')
    // L'attività: item nuovi ed engagement da quando la segui.
    expect(screen.getAllByText(/\+2 item · \+140 engagement/)[0]).toBeInTheDocument()
  })

  it('spuntare un passo manda la checklist aggiornata', async () => {
    const { calls } = monta()
    renderWithProviders(<SviluppoView />)

    await userEvent.click(
      await screen.findByRole('checkbox', { name: /Scrivi il prototipo/ }),
    )
    await waitFor(() => {
      const patch = calls.find((c) => c.method === 'PATCH')
      expect(patch?.body).toEqual({
        checklist: [
          { text: 'Scrivi il prototipo', done: true },
          { text: 'Compra il dominio', done: true },
        ],
      })
    })
  })

  it('un passo nuovo si aggiunge con Invio', async () => {
    const { calls } = monta()
    renderWithProviders(<SviluppoView />)

    const input = await screen.findByLabelText('Aggiungi un passo')
    await userEvent.type(input, 'Parla con tre utenti{Enter}')
    await waitFor(() => {
      const patch = calls.find((c) => c.method === 'PATCH')
      expect(patch?.body).toMatchObject({
        checklist: expect.arrayContaining([
          { text: 'Parla con tre utenti', done: false },
        ]),
      })
    })
  })

  it('cambiare stadio manda la PATCH giusta', async () => {
    const { calls } = monta()
    renderWithProviders(<SviluppoView />)

    await userEvent.click(
      await screen.findByRole('button', { name: 'Parcheggiata' }),
    )
    await waitFor(() => {
      const patch = calls.find((c) => c.method === 'PATCH')
      expect(patch?.body).toEqual({ stage: 'parked' })
    })
  })

  it('togliere dal tavolo è una DELETE, non tocca l\'idea', async () => {
    const { calls } = monta()
    renderWithProviders(<SviluppoView />)

    await userEvent.click(
      await screen.findByRole('button', {
        name: /Togli Runtime per agenti locali da Sviluppo/,
      }),
    )
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'DELETE')).toBe(true)
    })
  })

  it('tavolo vuoto: spiega come si entra, non un buco', async () => {
    monta([])
    renderWithProviders(<SviluppoView />)
    expect(await screen.findByText(/Il tavolo è vuoto/)).toBeInTheDocument()
  })

  it('elenca i segnali nuovi con il link, non solo il conteggio', async () => {
    monta()
    renderWithProviders(<SviluppoView />)
    const link = await screen.findByRole('link', {
      name: 'Show HN: agent runtime v2',
    })
    expect(link).toHaveAttribute('href', 'https://news.ycombinator.com/item?id=1')
    // E il contesto: il "perché conta" è sulla card.
    expect(
      screen.getByText('Risolve il deployment locale degli agenti.'),
    ).toBeInTheDocument()
  })

  it('checklist vuota: le mosse si generano al volo', async () => {
    const { calls } = monta([entry({ checklist: [] })])
    renderWithProviders(<SviluppoView />)

    await userEvent.click(
      await screen.findByRole('button', { name: /Genera le mosse/ }),
    )
    await waitFor(() => {
      expect(
        calls.some(
          (c) => c.method === 'POST' && c.url === '/workspace/1/moves',
        ),
      ).toBe(true)
    })
  })

  it('mosse generiche (422): non manda a controllare Ollama', async () => {
    /* Il 422 dice che Ollama HA risposto e la validazione ha scartato: il
       messaggio "Ollama è acceso?" manderebbe a cercare il guasto dove non c'è. */
    mockFetch({
      '/workspace/1/moves': new Response(
        JSON.stringify({ detail: 'Il modello non ha prodotto mosse specifiche' }),
        { status: 422 },
      ),
      '/workspace': [entry({ checklist: [] })],
    })
    renderWithProviders(<SviluppoView />)

    await userEvent.click(
      await screen.findByRole('button', { name: /Genera le mosse/ }),
    )

    expect(await screen.findByText(/passe-partout/)).toBeInTheDocument()
    expect(screen.queryByText(/Ollama è acceso/)).not.toBeInTheDocument()
  })
})
