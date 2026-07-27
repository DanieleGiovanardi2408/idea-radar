/* La trappola del focus.
 *
 * `aria-modal="true"` dichiara che fuori dalla modale non c'è niente: se il Tab
 * esce comunque, chi naviga da tastiera finisce a percorrere elementi che lo
 * screen reader ha appena detto di ignorare.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { describe, expect, it } from 'vitest'
import { useFocusTrap } from './useFocusTrap'

function Modale({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useFocusTrap(ref)
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="finestra">
      <button onClick={onClose}>primo</button>
      <button>secondo</button>
      <button>terzo</button>
    </div>
  )
}

function Pagina() {
  const [aperta, setAperta] = useState(false)
  return (
    <>
      <button onClick={() => setAperta(true)}>apri</button>
      <button>fuori</button>
      {aperta && <Modale onClose={() => setAperta(false)} />}
    </>
  )
}

describe('useFocusTrap', () => {
  it('porta il focus dentro appena la modale si apre', async () => {
    const user = userEvent.setup()
    render(<Pagina />)

    await user.click(screen.getByRole('button', { name: 'apri' }))

    expect(screen.getByRole('button', { name: 'primo' })).toHaveFocus()
  })

  it('dall’ultimo elemento il Tab torna al primo, non esce', async () => {
    const user = userEvent.setup()
    render(<Pagina />)
    await user.click(screen.getByRole('button', { name: 'apri' }))

    await user.tab() // secondo
    await user.tab() // terzo
    expect(screen.getByRole('button', { name: 'terzo' })).toHaveFocus()

    await user.tab() // sarebbe uscito
    expect(screen.getByRole('button', { name: 'primo' })).toHaveFocus()
  })

  it('Shift+Tab dal primo va all’ultimo', async () => {
    const user = userEvent.setup()
    render(<Pagina />)
    await user.click(screen.getByRole('button', { name: 'apri' }))

    await user.tab({ shift: true })

    expect(screen.getByRole('button', { name: 'terzo' })).toHaveFocus()
  })

  it('alla chiusura restituisce il focus a chi l’aveva', async () => {
    const user = userEvent.setup()
    render(<Pagina />)
    const apri = screen.getByRole('button', { name: 'apri' })
    await user.click(apri)

    await user.click(screen.getByRole('button', { name: 'primo' })) // chiude

    // Senza il ripristino il focus resterebbe sul body e la tastiera
    // ripartirebbe dall'inizio della pagina.
    expect(apri).toHaveFocus()
  })
})
