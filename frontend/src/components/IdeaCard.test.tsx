/* La card: apre il dettaglio al click, ma le azioni rapide NON lo aprono.
 * Lo stopPropagation su pin/scarta è il classico bug da regressione: senza,
 * ogni pin aprirebbe anche il dossier. */

import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { IdeaCard } from './IdeaCard'
import { fakeIdeaOut, mockFetch, renderWithProviders } from '../test/utils'

afterEach(() => {
  vi.unstubAllGlobals()
})

function monta(overrides = {}) {
  const calls = mockFetch({ '/ideas/1': fakeIdeaOut(overrides) }).calls
  const onSelect = vi.fn()
  renderWithProviders(
    <IdeaCard idea={fakeIdeaOut(overrides)} onSelect={onSelect} />,
  )
  return { calls, onSelect }
}

describe('IdeaCard', () => {
  it('mostra etichetta, tema e stato', () => {
    monta()
    expect(screen.getByText('Show HN: un runtime self-hosted per agenti')).toBeInTheDocument()
    expect(screen.getByText('Agenti locali')).toBeInTheDocument()
    expect(screen.getByText('proposed')).toBeInTheDocument()
  })

  it('il click sulla card apre il dettaglio', async () => {
    const { onSelect } = monta()
    await userEvent.click(screen.getByTestId('idea-card'))
    expect(onSelect).toHaveBeenCalledWith(1)
  })

  it('anche Invio da tastiera apre il dettaglio', async () => {
    const { onSelect } = monta()
    screen.getByTestId('idea-card').focus()
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith(1)
  })

  it('il pin manda la PATCH e NON apre il dettaglio', async () => {
    const { calls, onSelect } = monta()
    await userEvent.click(screen.getByRole('button', { name: 'Pinna in cima' }))
    expect(onSelect).not.toHaveBeenCalled()
    expect(calls).toContainEqual(
      expect.objectContaining({
        url: '/ideas/1',
        method: 'PATCH',
        body: { pinned: true },
      }),
    )
  })

  it('scarta manda la PATCH e NON apre il dettaglio', async () => {
    const { calls, onSelect } = monta()
    await userEvent.click(screen.getByRole('button', { name: 'Scarta' }))
    expect(onSelect).not.toHaveBeenCalled()
    expect(calls).toContainEqual(
      expect.objectContaining({
        url: '/ideas/1',
        method: 'PATCH',
        body: { dismissed: true },
      }),
    )
  })

  it('su un’idea scartata l’azione diventa "Ripristina"', async () => {
    const { calls } = monta({ dismissed_at: '2026-07-30T10:00:00' })
    expect(screen.getByText('scartata')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    expect(calls).toContainEqual(
      expect.objectContaining({ body: { dismissed: false } }),
    )
  })
})
