/* La vista principale: filtri e ricerca vivono sul SERVER, non sulla pagina
 * caricata. Questi test verificano il contratto — quali query partono — e la
 * paginazione onesta ("N di T" da X-Total-Count, "Carica altre" per il resto). */

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RadarView } from './RadarView'
import { fakeIdeaOut, mockFetch, renderWithProviders } from '../test/utils'
import type { IdeaOut } from '../types'

afterEach(() => {
  vi.unstubAllGlobals()
})

const BASE = {
  '/stats': { last_run: null, n_runs: 1 },
  '/profiles': [
    { name: 'ai-agents', label: 'Agenti AI', keywords: ['agent'], n_ideas: 2 },
    { name: 'iot', label: 'IoT', keywords: ['sensor'], n_ideas: 1 },
  ],
}

/** Risponde a /ideas come il backend: offset dalla query string, X-Total-Count
 *  con il totale. `perPage` basso simula la paginazione senza 100 idee finte. */
function archivio(tutte: IdeaOut[], perPage = 100) {
  return (_init?: RequestInit, url?: string) => {
    const params = new URLSearchParams((url ?? '').split('?')[1] ?? '')
    const offset = Number(params.get('offset') ?? 0)
    const rows = tutte.slice(offset, offset + perPage)
    return new Response(JSON.stringify(rows), {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'X-Total-Count': String(tutte.length),
      },
    })
  }
}

describe('RadarView', () => {
  it('chiede al server le proposte (status=proposed) e mostra le card', async () => {
    const { calls } = mockFetch({
      ...BASE,
      '/ideas': archivio([fakeIdeaOut({ id: 1, label: 'Runtime per agenti' })]),
    })
    renderWithProviders(<RadarView onSelect={() => {}} />)
    expect(await screen.findByTestId('idea-card')).toBeInTheDocument()
    // La lista è filtrata dal server; il quadrante chiede il vivo senza filtro.
    const urls = calls.map((c) => c.url)
    expect(urls.some((u) => u.includes('status=proposed'))).toBe(true)
    expect(urls.some((u) => u.startsWith('/ideas') && !u.includes('status='))).toBe(true)
  })

  it('il conteggio sul filtro attivo è il totale del server, non la pagina', async () => {
    mockFetch({
      ...BASE,
      // 2 righe per pagina, 3 in archivio: il totale vero è 3.
      '/ideas': archivio(
        [1, 2, 3].map((id) => fakeIdeaOut({ id, label: `Idea ${id}` })),
        2,
      ),
    })
    renderWithProviders(<RadarView onSelect={() => {}} />)
    const attivo = await screen.findByRole('button', { name: /Sopra soglia/ })
    await waitFor(() => expect(within(attivo).getByText('3')).toBeInTheDocument())
  })

  it('"Carica altre" chiede la pagina successiva con offset', async () => {
    const { calls } = mockFetch({
      ...BASE,
      '/ideas': archivio(
        [1, 2, 3].map((id) => fakeIdeaOut({ id, label: `Idea ${id}` })),
        2,
      ),
    })
    renderWithProviders(<RadarView onSelect={() => {}} />)
    const bottone = await screen.findByRole('button', { name: 'Carica altre (2 di 3)' })
    await userEvent.click(bottone)
    await waitFor(() => expect(screen.getAllByTestId('idea-card')).toHaveLength(3))
    expect(calls.some((c) => c.url.includes('offset=2'))).toBe(true)
    // Archivio esaurito: il bottone sparisce invece di promettere altro.
    expect(screen.queryByRole('button', { name: /Carica altre/ })).toBeNull()
  })

  it('la ricerca interroga il server (debounced), non filtra la pagina', async () => {
    const { calls } = mockFetch({
      ...BASE,
      '/ideas': archivio([fakeIdeaOut({ id: 1, label: 'Runtime per agenti' })]),
    })
    renderWithProviders(<RadarView onSelect={() => {}} />)
    await screen.findByTestId('idea-card')
    await userEvent.type(screen.getByLabelText("Cerca in tutto l'archivio"), 'lora')
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('q=lora'))).toBe(true),
    )
  })

  it('nessun risultato di ricerca: lo dice, senza svuotare il quadrante', async () => {
    mockFetch({
      ...BASE,
      '/ideas': (_init?: RequestInit, url?: string) => {
        const cerca = (url ?? '').includes('q=')
        const rows = cerca ? [] : [fakeIdeaOut({ id: 1 })]
        return new Response(JSON.stringify(rows), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Total-Count': String(rows.length),
          },
        })
      },
    })
    renderWithProviders(<RadarView onSelect={() => {}} />)
    await screen.findByTestId('idea-card')
    await userEvent.type(screen.getByLabelText("Cerca in tutto l'archivio"), 'zzz')
    expect(
      await screen.findByText('Nessuna idea trovata per "zzz" in tutto l\'archivio.'),
    ).toBeInTheDocument()
  })
})
