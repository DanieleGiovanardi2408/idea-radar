import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { SignalRhythm } from './SignalRhythm'
import { mockFetch, renderWithProviders } from '../test/utils'
import type { RhythmOut } from '../types'

function rhythm(overrides: Partial<RhythmOut> = {}): RhythmOut {
  const grid = Array.from({ length: 7 }, () => Array(24).fill(0))
  grid[4][15] = 30 // venerdì alle 15
  grid[0][9] = 3 // lunedì alle 9
  return {
    days: 28,
    n_items: 33,
    n_without_date: 0,
    grid,
    peak: 30,
    by_source: { hn: 33 },
    ...overrides,
  }
}

function show(data = rhythm()) {
  const mock = mockFetch({ '/rhythm': data, '/stats': { last_run: null } })
  renderWithProviders(<SignalRhythm />)
  return mock
}

describe('SignalRhythm', () => {
  it('disegna una cella per ogni ora della settimana', async () => {
    show()
    const cells = await screen.findAllByRole('button', { name: /ore \d+:/ })
    expect(cells).toHaveLength(7 * 24)
  })

  it('ogni cella dice quanti segnali contiene, anche a chi non vede i colori', async () => {
    show()
    expect(
      await screen.findByRole('button', { name: 'ven ore 15: 30 segnali' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'lun ore 9: 3 segnali' }),
    ).toBeInTheDocument()
  })

  it('mostra totale e picco, e li sostituisce col dettaglio al passaggio', async () => {
    const user = userEvent.setup()
    show()

    expect(await screen.findByText(/33 segnali · picco 30\/ora/)).toBeInTheDocument()

    await user.hover(screen.getByRole('button', { name: 'ven ore 15: 30 segnali' }))

    expect(await screen.findByText(/ven 15:00 · 30 segnali/)).toBeInTheDocument()
  })

  it('dice quanti segnali sono esclusi perché senza data', async () => {
    // Onestà: un item senza data non finisce in una cella inventata.
    show(rhythm({ n_without_date: 12 }))
    expect(
      await screen.findByText(/12 segnali senza data, esclusi/),
    ).toBeInTheDocument()
  })

  it('non annuncia esclusioni quando non ce ne sono', async () => {
    show(rhythm({ n_without_date: 0 }))
    await screen.findByText(/33 segnali/)
    expect(screen.queryByText(/senza data/)).not.toBeInTheDocument()
  })

  it('la finestra temporale si chiede al server', async () => {
    const user = userEvent.setup()
    const { calls } = show()

    await screen.findByText(/33 segnali/)
    expect(calls.some((c) => c.url.includes('days=28'))).toBe(true)

    await user.click(screen.getByRole('button', { name: '7g' }))

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('days=7'))).toBe(true),
    )
  })

  it('dichiara che le ore sono UTC', async () => {
    show()
    expect(await screen.findByText('ore UTC')).toBeInTheDocument()
  })
})
