/* Il quadrante da tastiera.
 *
 * I blip erano raggiungibili solo col mouse: `onClick` su un <g>, nessun
 * tabIndex, nessun nome. Chi naviga da tastiera vedeva un grafico e non poteva
 * entrarci. La scelta è il "roving tabindex" — una sola fermata di Tab per tutto
 * il gruppo, frecce per scorrere — perché sessanta blip sono sessanta fermate
 * per rileggere le stesse idee che la lista qui sotto già elenca.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RadarScope } from './RadarScope'
import { fakeIdeaOut } from '../test/utils'

function ideas(n: number) {
  return Array.from({ length: n }, (_, i) =>
    fakeIdeaOut({
      id: i + 1,
      label: `Idea ${i + 1}`,
      // Composite decrescente: l'ordine dei blip è per punteggio.
      composite: 0.9 - i * 0.1,
      status: i === 0 ? 'proposed' : 'processed',
      topic_id: 1,
    }),
  )
}

describe('RadarScope da tastiera', () => {
  it('il gruppo dei blip è una sola fermata di Tab', async () => {
    const user = userEvent.setup()
    render(<RadarScope ideas={ideas(4)} onSelect={() => {}} />)

    const blips = screen.getAllByRole('button')
    expect(blips).toHaveLength(4)
    // Uno solo è raggiungibile con Tab; gli altri con le frecce.
    expect(blips.filter((b) => b.getAttribute('tabindex') === '0')).toHaveLength(1)

    await user.tab()
    expect(blips[0]).toHaveFocus()
  })

  it('le frecce scorrono i blip e ciclano', async () => {
    const user = userEvent.setup()
    render(<RadarScope ideas={ideas(3)} onSelect={() => {}} />)
    const blips = screen.getAllByRole('button')

    await user.tab()
    await user.keyboard('{ArrowRight}')
    expect(blips[1]).toHaveFocus()

    await user.keyboard('{ArrowRight}{ArrowRight}')
    expect(blips[0]).toHaveFocus() // dall'ultimo torna al primo

    await user.keyboard('{ArrowLeft}')
    expect(blips[2]).toHaveFocus()

    await user.keyboard('{Home}')
    expect(blips[0]).toHaveFocus()
    await user.keyboard('{End}')
    expect(blips[2]).toHaveFocus()
  })

  it('Invio e spazio aprono l’idea sotto il focus', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<RadarScope ideas={ideas(3)} onSelect={onSelect} />)

    await user.tab()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith(1)

    await user.keyboard('{ArrowRight} ')
    expect(onSelect).toHaveBeenCalledWith(2)
  })

  it('ogni blip dice chi è e quanto vale, non "button"', () => {
    render(<RadarScope ideas={ideas(2)} onSelect={() => {}} />)

    // 0.9 di composite si legge 90, e "sopra soglia" distingue i due stati che
    // a schermo sono solo due colori diversi.
    expect(
      screen.getByRole('button', { name: 'Idea 1 — punteggio 90, sopra soglia' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Idea 2 — punteggio 80' }),
    ).toBeInTheDocument()
  })

  it('il tooltip appare anche col focus, non solo col mouse', async () => {
    const user = userEvent.setup()
    render(<RadarScope ideas={ideas(2)} onSelect={() => {}} />)

    await user.tab()

    // Il tooltip ripete l'etichetta: se comparisse solo all'hover, da tastiera
    // il contenuto non esisterebbe.
    const occorrenze = await screen.findAllByText('Idea 1')
    expect(occorrenze.length).toBeGreaterThan(0)
  })
})

describe('gli spicchi per tema', () => {
  const dueTemi = [
    fakeIdeaOut({ id: 1, profile: 'ai-agents', composite: 0.8 }),
    fakeIdeaOut({ id: 2, profile: 'iot', composite: 0.5, label: 'Sensore' }),
  ]
  const profili = [
    { name: 'ai-agents', label: 'Agenti AI' },
    { name: 'iot', label: 'IoT' },
  ]

  it('con più temi disegna le etichette degli spicchi', () => {
    render(<RadarScope ideas={dueTemi} onSelect={() => {}} profiles={profili} />)
    expect(screen.getByText('Agenti AI')).toBeInTheDocument()
    expect(screen.getByText('IoT')).toBeInTheDocument()
  })

  it('con un tema solo niente spicchi: il quadrante intero è lo spicchio', () => {
    render(
      <RadarScope
        ideas={[dueTemi[0]]}
        onSelect={() => {}}
        profiles={profili}
      />,
    )
    expect(screen.queryByText('Agenti AI')).toBeNull()
  })

  it('le idee senza profilo hanno lo spicchio "senza tema"', () => {
    render(
      <RadarScope
        ideas={[...dueTemi, fakeIdeaOut({ id: 3, profile: null })]}
        onSelect={() => {}}
        profiles={profili}
      />,
    )
    expect(screen.getByText('senza tema')).toBeInTheDocument()
  })
})
